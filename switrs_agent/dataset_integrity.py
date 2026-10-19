"""Integrity checks shared by dataset preparation and serving."""
import hashlib


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(8 << 20), b''):
            digest.update(block)
    return digest.hexdigest()
