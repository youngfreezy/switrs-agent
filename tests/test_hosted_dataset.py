import gzip
import hashlib
import io
import pytest
from scripts.fetch_hosted_dataset import unpack_verified


def test_corrupt_deployment_dataset_never_replaces_approved_file(tmp_path):
    target = tmp_path / 'data.duckdb'
    target.write_bytes(b'approved')
    with pytest.raises(ValueError, match='digest'):
        unpack_verified(io.BytesIO(gzip.compress(b'corrupt!')), target,
                        {'bytes': 8, 'sha256': hashlib.sha256(b'approved').hexdigest()})
    assert target.read_bytes() == b'approved'
    assert not target.with_suffix('.pending').exists()


def test_valid_build_preserves_every_verified_byte(tmp_path):
    raw = b'verified content'
    target = tmp_path / 'data.duckdb'
    unpack_verified(io.BytesIO(gzip.compress(raw)), target,
                    {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    assert target.read_bytes() == raw
