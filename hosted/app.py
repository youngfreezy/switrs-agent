"""Stateless hosted HTTP controller. No shared chat IDs, disk history, or CLI login."""
import asyncio
import json
import logging
import traceback
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from switrs_agent.publication import latest_certificate
from . import receipts
from .runtime import make_agent

ROOT = Path(__file__).resolve().parents[1]


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)

    @field_validator('message')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Enter a question')
        return value.strip()


class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int
    question: str = Field(max_length=4000)
    answer: str = Field(max_length=100000)
    signature: str = Field(max_length=64)


def sse(event):
    return 'data: ' + json.dumps(event, default=str) + '\n\n'


def create_app(*, agent_factory=make_agent, access_key=None, signing_key=None):
    app = FastAPI(title='SWITRS hosted agent', docs_url=None, redoc_url=None, openapi_url=None)
    access_key = access_key if access_key is not None else os.getenv('SWITRS_ACCESS_KEY', '')
    signing_key = signing_key if signing_key is not None else os.getenv('SWITRS_RECEIPT_KEY', '')
    # This bounds one worker; account-level spend limits are enforced by AI Gateway.
    slots = asyncio.Semaphore(2)

    @app.middleware('http')
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'hosted/static/index.html')

    @app.get('/assets/{name}')
    def asset(name: str):
        if name not in {'app.js', 'style.css'}:
            raise HTTPException(404)
        return FileResponse(ROOT / 'hosted/static' / name)

    @app.get('/api/status')
    def status():
        return {'ready': bool(access_key and signing_key), 'requires_access_key': True,
                'model': os.getenv('SWITRS_MODEL', 'anthropic/claude-opus-5-5'),
                'history': 'browser', 'max_model_turns': 8}

    @app.post('/api/verify')
    def verify(record: Receipt):
        return {'valid': receipts.verify(record.model_dump(), signing_key)}

    @app.post('/api/chat')
    async def chat(body: Question, request: Request):
        if not access_key or not signing_key:
            raise HTTPException(503, 'The agent is not configured yet.')
        supplied = request.headers.get('authorization', '')
        if not secrets.compare_digest(supplied.encode(), ('Bearer ' + access_key).encode()):
            raise HTTPException(401, 'Enter the access code to ask a question.')

        async def stream():
            events = []
            agent = None
            try:
                async with asyncio.timeout(240):
                    async with slots:
                        yield sse({'kind': 'status', 'text': 'Checking the dataset and investigating your question…'})
                        agent = agent_factory(oidc_token=request.headers.get('x-vercel-oidc-token'))
                        async for event in agent.ask(body.message):
                            if await request.is_disconnected():
                                return
                            # Provider errors can contain credentials, request contents or internal URLs.
                            if event.get('kind') == 'done' and event.get('error'):
                                event = {'kind': 'done', 'error': 'The model request failed. Please try again.'}
                                events = []
                            events.append(event)
                            yield sse(event)
                            if event.get('kind') == 'done' and event.get('error'):
                                break
            except Exception as exc:
                # Log locations and exception type, never prompts, tokens or provider error bodies.
                frames = [(Path(f.filename).name, f.lineno, f.name)
                          for f in traceback.extract_tb(exc.__traceback__)]
                logging.getLogger(__name__).error('Hosted request failed: %s %s', type(exc).__name__, frames)
                yield sse({'kind': 'done', 'error': 'The request could not finish. Please try again.'})
                events = []  # An interrupted turn must never retain a publication badge.
            finally:
                if agent is not None:
                    await agent.close()
                    toolkit = getattr(agent, 'toolkit', None)
                    if toolkit is not None:
                        toolkit.trace_path.unlink(missing_ok=True)
            answer = latest_certificate(events, body.message)
            yield sse({'kind': 'certified', 'final_answer': answer,
                       'receipt': receipts.sign(body.message, answer, signing_key)}
                      if answer else {'kind': 'uncertified'})

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'X-Accel-Buffering': 'no'})
    return app
