"""SQS consumer: bounded build failures, separate retention maintenance, no destructive queue recovery."""
import json
import hashlib
import random
import threading
import time
from urllib.parse import unquote_plus
from app.config import Settings
from app.store import Store
from app.domain import Domain, uid, Invalid
from app.knowledge import Knowledge, IndexBuildBusy
from app.embedding import error_details
from app.observability import emit


def reconcile(kb):
    return kb.reconcile()


class BuildDeferred(Exception): pass


class RetryGate:
    def __init__(self, cfg, clock=time.monotonic, jitter=random.uniform):
        self.cfg, self.clock, self.jitter = cfg, clock, jitter
        self.failures = 0; self.not_before = 0; self.suspended = False

    def allowed(self):
        return not self.suspended and self.clock() >= self.not_before

    def success(self):
        self.failures = 0; self.not_before = 0; self.suspended = False

    def failure(self, exc):
        if isinstance(exc, (IndexBuildBusy, BuildDeferred)):
            self.not_before = self.clock() + self.cfg.reconcile_interval
            return
        self.failures += 1
        delay = min(self.cfg.index_cooldown_cap, self.cfg.index_cooldown_base * 2**(self.failures-1))
        self.not_before = self.clock() + delay + self.jitter(0, min(30, delay*.1))
        # Persistent account/configuration failures require operator inspection, not endless API calls.
        self.suspended = not error_details(exc)['retryable'] or self.failures >= self.cfg.index_max_failures


class VisibilityHeartbeat:
    def __init__(self, sqs, cfg, message, trace, interval=90):
        self.sqs, self.cfg, self.message, self.trace = sqs, cfg, message, trace
        self.interval = interval; self.stop = threading.Event(); self.thread = None

    def extend(self):
        try:
            self.sqs.change_message_visibility(QueueUrl=self.cfg.queue,
                ReceiptHandle=self.message['ReceiptHandle'], VisibilityTimeout=300)
        except Exception as exc:
            emit(self.cfg, 'indexer', 'visibility_extension', 'failure', self.trace, **error_details(exc))

    def _run(self):
        while not self.stop.wait(self.interval): self.extend()

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True); self.thread.start(); return self

    def __exit__(self, *args):
        # Wait for an in-flight, SDK-timeout-bounded extension before applying a failure delay.
        self.stop.set(); self.thread.join()


def process_message(cfg, db, kb, sqs, message, gate, heartbeat=VisibilityHeartbeat):
    trace = uid(); started = time.monotonic(); building = False; generation = None
    receive_count = int(message.get('Attributes', {}).get('ApproximateReceiveCount', '1'))
    try:
        with heartbeat(sqs, cfg, message, trace):
            payload = json.loads(message['Body'])
            if not isinstance(payload, dict): raise Invalid('Invalid SQS JSON envelope.')
            records = payload.get('Records')
            if payload.get('Event') == 's3:TestEvent': records = []
            if not isinstance(records, list): raise Invalid('Missing S3 records.')
            accepted = []
            for record in records:
                if not isinstance(record, dict): raise Invalid('Invalid S3 record.')
                if record.get('eventSource') != 'aws:s3' or record.get('s3', {}).get('bucket', {}).get('name') != cfg.bucket: continue
                obj = record['s3']['object']; key = unquote_plus(obj['key'])
                if not key.startswith('knowledge/') or not key.endswith('.md'): continue
                seq = obj.get('sequencer')
                seen_key = 'sequencer:' + hashlib.sha256(key.encode()).hexdigest()
                seen, version = db.get(seen_key)
                if seq is not None:
                    try:
                        if seen and int(seq, 16) <= int(seen['value'], 16): continue
                        int(seq, 16)
                    except (ValueError, TypeError): raise Invalid('Invalid S3 sequencer.')
                accepted.append((seen_key, seq))
            if accepted:
                if not gate.allowed(): raise BuildDeferred('Index build cooldown/suspension active.')
                building = True
                state = reconcile(kb)  # One actual-source reconciliation per message, even with multiple records.
                generation = state.get('generation')
                gate.success(); building = False
                for seen_key, seq in accepted:
                    if seq is None: continue
                    seen, version = db.get(seen_key)
                    if not seen or int(seq, 16) > int(seen['value'], 16):
                        db.transact([(seen_key, {'value': seq}, version)])
            # No delete on parsing, build, state, or sequencer-write failure.
            sqs.delete_message(QueueUrl=cfg.queue, ReceiptHandle=message['ReceiptHandle'])
            emit(cfg, 'indexer', 'index_update', 'success', trace,
                 index_generation=generation, receive_count=receive_count,
                 reconciled=bool(accepted), duration_ms=round((time.monotonic()-started)*1000, 1))
            return True
    except Exception as exc:
        if building or isinstance(exc, BuildDeferred): gate.failure(exc)
        # Heartbeat has stopped before setting the failure visibility; it cannot undo this delay.
        delay = max(300, int(max(0, gate.not_before - gate.clock())))
        delay = min(43200, delay)
        try: sqs.change_message_visibility(QueueUrl=cfg.queue, ReceiptHandle=message['ReceiptHandle'], VisibilityTimeout=delay)
        except Exception as visibility_exc:
            emit(cfg, 'indexer', 'visibility_delay', 'failure', trace, **error_details(visibility_exc))
        emit(cfg, 'indexer', 'index_update', 'failure', trace,
             **error_details(exc), index_generation=generation, receive_count=receive_count,
             cooldown_seconds=delay, suspended=gate.suspended,
             duration_ms=round((time.monotonic()-started)*1000, 1))
        return False


class Worker:
    def __init__(self, cfg, db, kb, domain, sqs=None, clock=time.monotonic, sleep=time.sleep):
        self.cfg, self.db, self.kb, self.domain, self.sqs = cfg, db, kb, domain, sqs
        self.clock, self.sleep = clock, sleep; self.gate = RetryGate(cfg, clock)
        self.next_reconcile = 0; self.next_purge = 0; self.suspension_logged = False

    def tick(self):
        # Retention cleanup continues even when embedding is cooling down or suspended.
        if self.clock() >= self.next_purge:
            self.next_purge = self.clock() + 60
            try: self.domain.purge_due()
            except Exception as exc: emit(self.cfg, 'indexer', 'purge_due', 'failure', uid(), **error_details(exc))
        if self.gate.allowed() and self.clock() >= self.next_reconcile:
            self.next_reconcile = self.clock() + self.cfg.reconcile_interval
            try: reconcile(self.kb); self.gate.success()
            except Exception as exc:
                self.gate.failure(exc)
                emit(self.cfg, 'indexer', 'reconcile', 'failure', uid(), **error_details(exc),
                     cooldown_seconds=round(max(0, self.gate.not_before-self.clock()), 1),
                     suspended=self.gate.suspended)
        if not self.gate.allowed():
            if self.gate.suspended and not self.suspension_logged:
                emit(self.cfg, 'indexer', 'index_builds_suspended', 'waiting', uid(),
                     consecutive_failures=self.gate.failures, requires_operator_review=True)
                self.suspension_logged = True
            # Do not consume otherwise valid events into DLQ while the whole build path is unavailable.
            self.sleep(5); return
        self.suspension_logged = False
        if self.sqs is None: self.sleep(5); return
        try:
            result = self.sqs.receive_message(QueueUrl=self.cfg.queue, MaxNumberOfMessages=1,
                WaitTimeSeconds=20, VisibilityTimeout=300,
                MessageSystemAttributeNames=['ApproximateReceiveCount'])
            for message in result.get('Messages', []):
                process_message(self.cfg, self.db, self.kb, self.sqs, message, self.gate)
        except Exception as exc:
            emit(self.cfg, 'indexer', 'sqs_receive', 'failure', uid(), **error_details(exc)); self.sleep(5)


def run():
    cfg = Settings(); cfg.validate(); db = Store(cfg); kb = Knowledge(cfg, db); domain = Domain(db)
    sqs = None
    if cfg.mode == 'aws':
        import boto3
        from botocore.config import Config
        sqs = boto3.client('sqs', region_name=cfg.region,
            config=Config(connect_timeout=5, read_timeout=30,
                          retries={'mode': 'standard', 'total_max_attempts': 3}))
    worker = Worker(cfg, db, kb, domain, sqs)
    while True: worker.tick()


if __name__ == '__main__': run()
