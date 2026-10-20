"""Malformed browser receipts must produce a rejection, never an HTTP 500."""
import pytest

from hosted import receipts
from test_hosted_app import client


@pytest.mark.parametrize('signature', ['é' * 64, '😀' * 64, 'z' * 64, '' , '0' * 64])
def test_invalid_signature_is_a_normal_false_verdict(signature):
    record = receipts.sign('How many collisions?', 'Checked answer.', 'test-signing')
    record['signature'] = signature
    response = client().post('/api/verify', json=record)
    assert response.status_code == 200
    assert response.json() == {'valid': False}
