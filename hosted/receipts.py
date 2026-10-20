"""Authenticate saved browser answers without accepting browser state as evidence."""
import hashlib
import hmac
import json
import re


def signature(record: dict, key: str) -> str:
    content = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return hmac.new(key.encode(), b'switrs-receipt-v1\0' + content, hashlib.sha256).hexdigest()


def sign(question: str, answer: str, key: str, *, resolved_question: str | None = None,
         status: str = 'unknown') -> dict:
    record = {'version': 1, 'question': question, 'answer': answer}
    if resolved_question is not None:
        record.update(version=2, resolved_question=resolved_question, status=status)
    return {**record, 'signature': signature(record, key)}


def verify(record: dict, key: str) -> bool:
    unsigned = {k: v for k, v in record.items() if k != 'signature'}
    supplied = record.get('signature')
    return bool(key and isinstance(supplied, str) and re.fullmatch(r'[0-9a-f]{64}', supplied)
                and hmac.compare_digest(supplied, signature(unsigned, key)))
