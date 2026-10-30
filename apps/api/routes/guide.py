"""Guide: personal-key providers and grounded, tool-free questions."""
import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from apps.api.auth import current_user_id
from apps.api.guide import service

router = APIRouter()


class AskBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(max_length=service.MAX_QUESTION_CHARS)
    route: str = Field(max_length=300)
    provider: str = Field(max_length=40)
    model: str = Field(max_length=200)
    consent: Literal[True]

    @field_validator('question')
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('question is required')
        return value.strip()


@router.get('/guide/providers')
def guide_providers(user_id: str = Depends(current_user_id)):
    return {'providers': service.available_providers(user_id)}


@router.post('/guide/ask')
async def guide_ask(body: AskBody, user_id: str = Depends(current_user_id)):
    try:
        await run_in_threadpool(service.prepare, user_id, body.provider, body.model)
    except service.GuideError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    # The worker thread releases the user's slot when the provider call really
    # ends, so a timed-out call still blocks a second concurrent request.
    call = service._executor.submit(service.ask, user_id, body.question, body.route, body.provider, body.model)
    try:
        return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(call)), service.TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        if call.cancel():  # never started, so its own finally will not release
            service._release(user_id)
        return service.timed_out(body.question, body.route)
