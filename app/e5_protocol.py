"""Candidate boundary logic only. Not integrated into RoleOps and not a model benchmark."""
import hashlib
import json
import math
import re


class ContractError(ValueError):
    pass


def cache_key(contract, purpose, text):
    if purpose not in ('query', 'document') or not isinstance(text, str) or not text.strip():
        raise ContractError('Invalid embedding purpose/text')
    required = ('provider', 'revision', 'weights_sha256', 'dimensions', 'pooling',
                'normalize', 'query_prefix', 'document_prefix', 'chunking_version')
    if any(k not in contract for k in required):
        raise ContractError('Incomplete embedding contract')
    if not re.fullmatch(r'[a-f0-9]{40}', contract['revision']):
        raise ContractError('Unpinned model revision')
    if not re.fullmatch(r'[a-f0-9]{64}', contract['weights_sha256']):
        raise ContractError('Unpinned weights')
    if contract['dimensions'] != 384 or contract['normalize'] is not True:
        raise ContractError('Candidate E5 geometry mismatch')
    payload = {'schema': 3, 'contract': contract, 'purpose': purpose, 'text': text}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def validate_vector(vector):
    if not isinstance(vector, list) or len(vector) != 384:
        raise ContractError('Expected 384-dimensional E5 vector')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
           for x in vector):
        raise ContractError('Non-finite or invalid vector value')
    if abs(math.sqrt(math.fsum(x*x for x in vector))-1) > 0.01:
        raise ContractError('Vector must be L2 normalized')
    return vector


def split_by_offsets(text, offsets, body_tokens=480, overlap_tokens=48):
    """Use real tokenizer offsets in deployment. Re-tokenize each prefixed slice before inference."""
    if not isinstance(text, str) or not 0 <= overlap_tokens < body_tokens <= 480:
        raise ContractError('Invalid chunking parameters')
    previous_start, previous_end = -1, -1
    for start, end in offsets:
        if not (0 <= start <= end <= len(text)) or start < previous_start or end < previous_end:
            raise ContractError('Invalid tokenizer offset sequence')
        previous_start, previous_end = start, end
    if not offsets:
        if text.strip():
            raise ContractError('Non-empty text has no tokens')
        return []
    pieces = []
    for position in range(0, len(offsets), body_tokens-overlap_tokens):
        final = min(position+body_tokens, len(offsets))
        start = 0 if position == 0 else offsets[position][0]
        end = len(text) if final == len(offsets) else offsets[final][0]
        if end <= start:
            raise ContractError('Empty token slice')
        pieces.append({'start': start, 'end': end, 'text': text[start:end]})
        if final == len(offsets):
            break
    return pieces


def parse_generation(response, allowed_systems):
    """Return a proposal only; this function never calls MCP or changes permissions."""
    choices = response.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:
        raise ContractError('Expected one generation choice')
    choice = choices[0]
    if choice.get('finish_reason') not in ('stop', 'tool_calls'):
        raise ContractError('Incomplete or unsupported generation finish')
    message = choice.get('message', {})
    if message.get('role') != 'assistant':
        raise ContractError('Invalid response role')
    answer = message.get('content') or ''
    if not isinstance(answer, str) or len(answer) > 8000:
        raise ContractError('Invalid answer')
    calls = message.get('tool_calls') or []
    if not isinstance(calls, list) or len(calls) > 1:
        raise ContractError('Expected at most one proposal')
    proposal = None
    if calls:
        call = calls[0]
        function = call.get('function', {})
        if call.get('type') != 'function' or function.get('name') != 'create_access_request':
            raise ContractError('Unexpected tool')
        raw = function.get('arguments')
        if not isinstance(raw, str):
            raise ContractError('Expected JSON string tool arguments')
        try:
            arguments = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ContractError('Malformed tool arguments') from exc
        if not isinstance(arguments, dict) or set(arguments) != {'system_id', 'reason'}:
            raise ContractError('Unexpected tool arguments')
        system = arguments['system_id']
        reason = arguments['reason']
        if not isinstance(system, str) or system not in allowed_systems:
            raise ContractError('Out-of-scope system')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ContractError('Invalid proposal reason')
        proposal = {'system_id': system, 'reason': reason}
    if not answer.strip() and proposal is None:
        raise ContractError('Empty generation')
    return {'answer': answer, 'proposal': proposal}
