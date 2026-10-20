import gzip
import hashlib
import io
import json
import pytest
from scripts import fetch_hosted_dataset as build
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


def setup_build(monkeypatch, tmp_path):
    raw = b'verified content'
    (tmp_path / 'hosted').mkdir()
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/switrs.duckdb').write_bytes(b'previous verified copy')
    (tmp_path / 'hosted/dataset_meta.json').write_text(json.dumps({
        'dataset_version': 'fixture',
        'duckdb_file': {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}}))
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    monkeypatch.setenv('SWITRS_DATASET_URL', 'https://fixture.private.blob.vercel-storage.com/data.gz')
    monkeypatch.setenv('BLOB_READ_WRITE_TOKEN', 'fixture-token')
    monkeypatch.setattr('time.sleep', lambda seconds: None)
    return raw, tmp_path / 'data/switrs.duckdb'


def test_truncated_download_restarts_and_verifies_from_byte_zero(monkeypatch, tmp_path):
    raw, target = setup_build(monkeypatch, tmp_path)
    sources = []
    def download(*args, **kwargs):
        content = gzip.compress(raw)
        source = io.BytesIO(content[:-6] if not sources else content)
        sources.append(source)
        return source
    monkeypatch.setattr(build.urllib.request, 'urlopen', download)
    build.main()
    assert len(sources) == 2 and all(s.closed for s in sources)
    assert target.read_bytes() == raw
    assert not target.with_suffix('.pending').exists()


def test_repeated_truncation_stops_without_replacing_the_old_copy(monkeypatch, tmp_path):
    raw, target = setup_build(monkeypatch, tmp_path)
    sources = []
    def download(*args, **kwargs):
        source = io.BytesIO(gzip.compress(raw)[:-6])
        sources.append(source)
        return source
    monkeypatch.setattr(build.urllib.request, 'urlopen', download)
    with pytest.raises(EOFError):
        build.main()
    assert len(sources) == 3 and all(s.closed for s in sources)
    assert target.read_bytes() == b'previous verified copy'
    assert not target.with_suffix('.pending').exists()
    assert not (target.parent / 'dataset_meta.json').exists()


def test_digest_mismatch_is_not_treated_as_a_transient_download(monkeypatch, tmp_path):
    raw, target = setup_build(monkeypatch, tmp_path)
    calls = []
    def download(*args, **kwargs):
        calls.append(1)
        return io.BytesIO(gzip.compress(b'x' * len(raw)))
    monkeypatch.setattr(build.urllib.request, 'urlopen', download)
    with pytest.raises(ValueError, match='digest'):
        build.main()
    assert len(calls) == 1
    assert target.read_bytes() == b'previous verified copy'


def test_authentication_failure_is_not_retried(monkeypatch, tmp_path):
    _, target = setup_build(monkeypatch, tmp_path)
    calls = []
    def download(*args, **kwargs):
        calls.append(1)
        raise build.urllib.error.HTTPError('https://fixture.example', 401, 'Unauthorized', {}, None)
    monkeypatch.setattr(build.urllib.request, 'urlopen', download)
    with pytest.raises(build.urllib.error.HTTPError):
        build.main()
    assert len(calls) == 1
    assert target.read_bytes() == b'previous verified copy'


@pytest.mark.parametrize('failure', [
    TimeoutError(),
    build.IncompleteRead(b'partial'),
    build.urllib.error.URLError('connection lost'),
    build.urllib.error.HTTPError('https://fixture.example', 503, 'Unavailable', {}, None),
])
def test_transient_transfer_error_can_recover(monkeypatch, tmp_path, failure):
    raw, target = setup_build(monkeypatch, tmp_path)
    calls = []
    def download(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise failure
        return io.BytesIO(gzip.compress(raw))
    monkeypatch.setattr(build.urllib.request, 'urlopen', download)
    build.main()
    assert len(calls) == 2
    assert target.read_bytes() == raw
