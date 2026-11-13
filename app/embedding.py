"""Bounded Bedrock retries and pacing shared by containers on ROLEOPS_DATA."""
import json
import os
import random
import re
import time
import uuid
from filelock import FileLock
from .observability import emit


RETRY_CODES = frozenset({
    'ThrottlingException', 'TooManyRequestsException', 'ServiceUnavailableException',
    'InternalServerException', 'InternalFailure', 'ModelTimeoutException', 'ModelNotReadyException',
    'RequestTimeout', 'RequestTimeoutException',
})
NETWORK_ERRORS = frozenset({'ConnectTimeoutError', 'ReadTimeoutError',
                           'EndpointConnectionError', 'ConnectionClosedError'})


def error_details(exc):
    """Never expose provider messages, which may echo document text or secrets."""
    if isinstance(exc, EmbeddingFailure):
        return {'error_type': type(exc).__name__, 'error_code': exc.code,
                'retryable': exc.retryable, 'retry_count': max(0, exc.attempts - 1)}
    response = getattr(exc, 'response', {}) or {}
    code = response.get('Error', {}).get('Code', type(exc).__name__)
    code = code if re.fullmatch(r'[A-Za-z0-9_]{1,80}', str(code)) else 'ProviderError'
    status = response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0)
    # Explicit permanent errors take precedence over generic HTTP status.
    permanent = code in {'AccessDeniedException', 'ValidationException', 'ResourceNotFoundException',
                         'ServiceQuotaExceededException', 'UnrecognizedClientException', 'InvalidSignatureException'}
    retryable = not permanent and (code in RETRY_CODES or status == 429 or
                                  isinstance(status, int) and 500 <= status <= 599 or
                                  type(exc).__name__ in NETWORK_ERRORS)
    return {'error_type': type(exc).__name__, 'error_code': code, 'retryable': bool(retryable)}


class EmbeddingFailure(Exception):
    def __init__(self, code, retryable, attempts, duration_ms):
        self.code, self.retryable, self.attempts, self.duration_ms = code, retryable, attempts, duration_ms
        super().__init__(f'Embedding failed: {code}; attempts={attempts}; retryable={retryable}')


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()

def atomic_bytes(path, value):
    temporary = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('wb') as stream:
            stream.write(value); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


class EmbeddingClient:
    def __init__(self, cfg, root, client=None, sleep=time.sleep, clock=time.monotonic,
                 wall=time.time, jitter=random.uniform):
        self.cfg, self.root, self.client = cfg, root, client
        self.sleep, self.clock, self.wall, self.jitter = sleep, clock, wall, jitter
        self.root.mkdir(parents=True, exist_ok=True)

    def _client(self):
        if self.client is None:
            import boto3
            from botocore.config import Config
            self.client = boto3.client('bedrock-runtime', region_name=self.cfg.region,
                config=Config(connect_timeout=self.cfg.embed_connect_timeout,
                              read_timeout=self.cfg.embed_read_timeout,
                              retries={'mode': 'standard', 'total_max_attempts': 1}))
        return self.client

    def _pace(self, deadline):
        # All new clients use the same on-volume slot, including queries and manual jobs.
        with FileLock(str(self.root/'embedding-rate.lock'), timeout=self.cfg.index_lock_timeout):
            path = self.root/'embedding-rate.json'
            previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            delay = max(0, float(previous.get('next_at', 0)) - self.wall())
            if self.clock() + delay >= deadline: return False
            if delay: self.sleep(delay)
            atomic_json(path, {'next_at': self.wall() + self.cfg.embed_min_interval})
            return True

    def invoke(self, text, trace_id=None):
        trace_id = trace_id or uuid.uuid4().hex
        started = self.clock(); deadline = started + self.cfg.embed_retry_budget
        attempts = 0; last = {'error_code': 'RetryBudgetExceeded', 'retryable': True}
        while attempts < self.cfg.embed_max_attempts:
            if self.clock() >= deadline or not self._pace(deadline): break
            attempts += 1
            try:
                response = self._client().invoke_model(modelId=self.cfg.embed_model,
                    contentType='application/json', accept='application/json',
                    body=json.dumps({'inputText': text, 'dimensions': self.cfg.embed_dimensions,
                                     'normalize': self.cfg.embed_normalize}))
                body = response['body']
                try: vector = json.loads(body.read())['embedding']
                finally:
                    if hasattr(body, 'close'): body.close()
                emit(self.cfg, 'indexer', 'embedding', 'success', trace_id,
                     {'EmbeddingRequests': attempts, 'EmbeddingRetries': attempts-1},
                     model_id=self.cfg.embed_model, retry_count=attempts-1,
                     duration_ms=round((self.clock()-started)*1000, 1))
                return vector
            except Exception as exc:
                last = error_details(exc)
                if not last['retryable'] or attempts >= self.cfg.embed_max_attempts: break
                cap = min(self.cfg.embed_backoff_cap, self.cfg.embed_backoff_base * 2**(attempts-1))
                delay = self.jitter(0, cap)
                if self.clock() + delay >= deadline: break
                emit(self.cfg, 'indexer', 'embedding_retry', 'waiting', trace_id,
                     model_id=self.cfg.embed_model, retry_count=attempts,
                     error_code=last['error_code'], wait_ms=round(delay*1000, 1))
                self.sleep(delay)
        elapsed = round((self.clock()-started)*1000, 1)
        emit(self.cfg, 'indexer', 'embedding', 'failure', trace_id,
             {'EmbeddingRequests': attempts, 'EmbeddingRetries': max(0, attempts-1), 'EmbeddingFailures': 1},
             model_id=self.cfg.embed_model, retry_count=max(0, attempts-1),
             error_code=last['error_code'], retryable=last['retryable'], duration_ms=elapsed)
        # The scheduling budget cannot cancel an in-flight request; socket timeouts bound that attempt.
        raise EmbeddingFailure(last['error_code'], last['retryable'], attempts, elapsed)
