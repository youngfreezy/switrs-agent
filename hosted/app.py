"""Stateless hosted HTTP controller. No shared chat IDs, disk history, or CLI login."""
import asyncio
import json
import logging
import traceback
import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from switrs_agent.publication import latest_certified_result
from . import receipts
from .followups import resolve
from .runtime import make_agent
from .completions import context_for, generate_completion, validate_completion, while_connected
from .suggestions import candidates_for, rank_candidates, select_ranked

ROOT = Path(__file__).resolve().parents[1]


class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int
    question: str = Field(max_length=4000)
    answer: str = Field(max_length=100000)
    signature: str = Field(max_length=64)
    resolved_question: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, max_length=40)


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)
    previous: Receipt | None = None

    @field_validator('message')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('Enter a question')
        return value.strip()


def sse(event):
    return 'data: ' + json.dumps(event, default=str) + '\n\n'


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prefix: str = Field(min_length=4, max_length=1000)
    previous: Receipt | None = None

    @field_validator('prefix')
    @classmethod
    def valid_prefix(cls, value):
        if len(value.strip()) < 4 or any(ord(c) < 32 for c in value):
            raise ValueError('Enter a single-line question prefix')
        return value


class CompletionResponse(BaseModel):
    prefix: str
    completion: str


class SuggestionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    draft: str = Field(default='', max_length=1000)
    previous: Receipt | None = None

    @field_validator('draft')
    @classmethod
    def single_line(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError('Enter a single-line draft')
        return value


class SuggestionResponse(BaseModel):
    draft: str
    suggestions: list[str]
    source: Literal['jev', 'fallback']


def create_app(*, agent_factory=make_agent, completion_fn=generate_completion,
               ranking_fn=rank_candidates, access_key=None, signing_key=None):
    app = FastAPI(title='SWITRS hosted agent', docs_url=None, redoc_url=None, openapi_url=None)
    access_key = access_key if access_key is not None else os.getenv('SWITRS_ACCESS_KEY', '')
    signing_key = signing_key if signing_key is not None else os.getenv('SWITRS_RECEIPT_KEY', '')
    # This bounds one worker; account-level spend limits are enforced by AI Gateway.
    slots = asyncio.Semaphore(2)
    completion_slots = asyncio.Semaphore(2)
    suggestion_slots = asyncio.Semaphore(2)

    def authorize(request):
        if not access_key or not signing_key:
            raise HTTPException(503, 'The agent is not configured yet.')
        supplied = request.headers.get('authorization', '')
        if not secrets.compare_digest(supplied.encode(), ('Bearer ' + access_key).encode()):
            raise HTTPException(401, 'Enter the access code to ask a question.')

    def authenticate_previous(record):
        previous = record.model_dump(exclude_none=True) if record else None
        if previous is not None and not receipts.verify(previous, signing_key):
            raise HTTPException(422, 'The prior answer could not be authenticated. Ask a complete question.')
        return previous

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
        if name not in {'app.js', 'activity.js', 'completion.js', 'suggestions.js', 'style.css'}:
            raise HTTPException(404)
        return FileResponse(ROOT / 'hosted/static' / name)

    @app.get('/api/status')
    def status():
        return {'ready': bool(access_key and signing_key), 'requires_access_key': True,
                'model': os.getenv('SWITRS_MODEL', 'anthropic/claude-opus-5-5'),
                'history': 'browser', 'max_model_turns': 8}

    @app.post('/api/verify')
    def verify(record: Receipt):
        return {'valid': receipts.verify(record.model_dump(exclude_none=True), signing_key)}

    @app.post('/api/completions', response_model=CompletionResponse)
    async def complete(body: CompletionRequest, request: Request):
        authorize(request)
        previous = authenticate_previous(body.previous)
        empty = {'prefix': body.prefix, 'completion': ''}
        if body.prefix.rstrip().endswith('?') or completion_slots.locked():
            return empty
        try:
            async with asyncio.timeout(6):
                async with completion_slots:
                    if await request.is_disconnected():
                        return empty
                    context = context_for(previous)
                    text = await while_connected(request, completion_fn(
                        prefix=body.prefix, context=context,
                        oidc_token=request.headers.get('x-vercel-oidc-token')))
                    return {'prefix': body.prefix,
                            'completion': validate_completion(body.prefix, text, context)}
        except Exception as exc:
            logging.getLogger(__name__).warning('Autocomplete unavailable: %s', type(exc).__name__)
            return empty

    @app.post('/api/suggestions', response_model=SuggestionResponse)
    async def suggest(body: SuggestionRequest, request: Request):
        authorize(request)
        previous = authenticate_previous(body.previous)
        prior_question = (previous.get('resolved_question') or previous['question']) if (
            previous and previous.get('status') in {'answered', 'no_matching_records'}) else None
        fallback = {'draft': body.draft, 'suggestions': [], 'source': 'fallback'}
        candidates = candidates_for(body.draft, prior_question)
        if not candidates or suggestion_slots.locked():
            return fallback
        try:
            async with asyncio.timeout(5):
                async with suggestion_slots:
                    if await request.is_disconnected():
                        return fallback
                    result = await while_connected(request, ranking_fn(
                        draft=body.draft, previous_question=prior_question, candidates=candidates,
                        oidc_token=request.headers.get('x-vercel-oidc-token')))
                    selected = select_ranked(candidates, result)
                    return {'draft': body.draft, 'suggestions': selected,
                            'source': 'jev' if selected else 'fallback'}
        except Exception as exc:
            logging.getLogger(__name__).warning('Question ranking unavailable: %s', type(exc).__name__)
            return fallback

    @app.post('/api/chat')
    async def chat(body: Question, request: Request):
        authorize(request)
        question = body.message
        previous = authenticate_previous(body.previous)
        if previous is not None:
            question = resolve(question, previous.get('resolved_question') or previous['question'])

        async def stream():
            events = []
            agent = None
            try:
                async with asyncio.timeout(240):
                    async with slots:
                        yield sse({'kind': 'status', 'text': 'Checking the dataset and investigating your question…'})
                        agent = agent_factory(oidc_token=request.headers.get('x-vercel-oidc-token'))
                        async for event in agent.ask(question):
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
            result = latest_certified_result(events, question)
            if result:
                answer, status = result['final_answer'], result.get('status', 'unknown')
                yield sse({'kind': 'certified', 'final_answer': answer, 'status': status,
                           'resolved_question': question,
                           'receipt': receipts.sign(body.message, answer, signing_key,
                                                    resolved_question=question, status=status)})
            else:
                yield sse({'kind': 'uncertified'})

        return StreamingResponse(stream(), media_type='text/event-stream',
                                 headers={'X-Accel-Buffering': 'no'})
    return app
