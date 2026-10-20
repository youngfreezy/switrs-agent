"""Authenticate saved browser answers without accepting browser state as evidence."""
import hashlib
import hmac
import json


def signature(record: dict, key: str) -> str:
    content = json.dumps(record, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return hmac.new(key.encode(), b'switrs-receipt-v1\0' + content, hashlib.sha256).hexdigest()


def sign(question: str, answer: str, key: str) -> dict:
    record = {'version': 1, 'question': question, 'answer': answer}
    return {**record, 'signature': signature(record, key)}


def verify(record: dict, key: str) -> bool:
    unsigned = {k: v for k, v in record.items() if k != 'signature'}
    supplied = record.get('signature')
    return bool(key and isinstance(supplied, str) and len(supplied) == 64
                and hmac.compare_digest(supplied, signature(unsigned, key)))
