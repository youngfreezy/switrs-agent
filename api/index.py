"""Vercel ASGI entry point."""
import os

os.environ.setdefault('SWITRS_OUTPUT_DIR', '/tmp/switrs-outputs')
os.environ.setdefault('SWITRS_MEMORY_LIMIT', '1GB')
os.environ.setdefault('SWITRS_TEMP_LIMIT', '0GB')
os.environ.setdefault('SWITRS_THREADS', '2')

from hosted.app import create_app

app = create_app()
