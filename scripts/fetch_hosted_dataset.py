"""Install the pinned, fully reconciled snapshot during a Vercel build."""
import gzip
import hashlib
import json
import os
import time
from http.client import IncompleteRead
from pathlib import Path
import urllib.error
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def unpack_verified(source, target, expected):
    pending = target.with_suffix('.pending')
    digest, size = hashlib.sha256(), 0
    try:
        with gzip.GzipFile(fileobj=source) as src, pending.open('wb') as dst:
            while block := src.read(1024 * 1024):
                size += len(block)
                if size > expected['bytes']:
                    raise ValueError('Dataset size exceeds the approved snapshot')
                digest.update(block)
                dst.write(block)
        if size != expected['bytes'] or digest.hexdigest() != expected['sha256']:
            raise ValueError('Dataset content digest does not match the approved snapshot')
        pending.replace(target)
    finally:
        pending.unlink(missing_ok=True)


def main():
    manifest = json.loads((ROOT / 'hosted/dataset_meta.json').read_text())
    url = os.environ['SWITRS_DATASET_URL']
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.private.blob.vercel-storage.com'):
        raise ValueError('Dataset must come from private Vercel Blob storage')
    (ROOT / 'data').mkdir(exist_ok=True)
    request = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + os.environ['BLOB_READ_WRITE_TOKEN']})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=240) as source:
                unpack_verified(source, ROOT / 'data/switrs.duckdb', manifest['duckdb_file'])
            break
        except (EOFError, IncompleteRead, TimeoutError, ConnectionError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                raise
            if attempt == 2:
                raise
            # Restart the stream. Never resume a partial gzip or bypass its content checks.
            print(f'Dataset transfer interrupted; retrying ({attempt + 2}/3).', flush=True)
            time.sleep(2 ** attempt)
    (ROOT / 'data/dataset_meta.json').write_text(json.dumps(manifest))
    print('Installed verified dataset:', manifest['dataset_version'], manifest['duckdb_file']['sha256'])


if __name__ == '__main__':
    main()
