"""App setup requires Clerk; provider callbacks require a connection secret."""
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.concurrency import run_in_threadpool
from apps.api.auth import current_user_id
from apps.api.messaging import connections as c, providers, service, store

router=APIRouter()
Channel=Literal['whatsapp','telegram']


class Setup(BaseModel):
    model_config=ConfigDict(extra='forbid')
    server_url:str=Field('',max_length=500)
    api_key:SecretStr=SecretStr('')
    bot_token:SecretStr=SecretStr('')
    replace_webhook:bool=False


class Pair(BaseModel):
    phone:str=Field('',max_length=30)
    code:str=Field('',max_length=20)


class Preferences(BaseModel):
    alerts:bool=False
    approvals:bool=False


def _call(fn,*args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None
    except providers.ProviderError as exc:
        raise HTTPException(502,str(exc)) from None


@router.post('/messaging/hooks/{cid}')
async def webhook(cid:str, request:Request):
    doc=store.by_id(cid)
    header='x-telegram-bot-api-secret-token' if doc and doc['channel']=='telegram' else 'x-drishti-webhook-secret'
    secret=request.headers.get(header,'')
    try:
        service.authorize(cid,secret)
    except PermissionError:
        raise HTTPException(403,'Invalid messaging callback') from None
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>65536:
            raise HTTPException(413,'Messaging callback is too large')
    try:
        payload=json.loads(raw)
    except (ValueError,UnicodeDecodeError):
        raise HTTPException(400,'Invalid messaging callback JSON') from None
    try:
        return await run_in_threadpool(service.receive,cid,secret,payload)
    except PermissionError:
        raise HTTPException(403,'Invalid messaging callback') from None
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None


@router.get('/messaging/{channel}')
def status(channel:Channel,uid:str=Depends(current_user_id)):
    return c.status(uid,channel)


@router.put('/messaging/{channel}')
def setup(channel:Channel,body:Setup,uid:str=Depends(current_user_id)):
    if channel=='whatsapp':
        return _call(c.configure_whatsapp,uid,body.server_url,body.api_key.get_secret_value())
    return _call(c.configure_telegram,uid,body.bot_token.get_secret_value(),body.replace_webhook)


@router.post('/messaging/{channel}/pair')
def pair(channel:Channel,body:Pair,uid:str=Depends(current_user_id)):
    return _call(c.pair_whatsapp,uid,body.phone) if channel=='whatsapp' else _call(c.begin_telegram_pair,uid)


@router.post('/messaging/{channel}/confirm')
def confirm(channel:Channel,body:Pair,uid:str=Depends(current_user_id)):
    return _call(c.confirm_pair,uid,channel,body.code)


@router.post('/messaging/whatsapp/recipient')
def recipient(body:Pair,uid:str=Depends(current_user_id)):
    return _call(c.verify_recipient,uid,body.phone)


@router.post('/messaging/{channel}/refresh')
def refresh(channel:Channel,uid:str=Depends(current_user_id)):
    return _call(c.refresh_whatsapp,uid) if channel=='whatsapp' else c.status(uid,channel)


@router.put('/messaging/{channel}/preferences')
def preferences(channel:Channel,body:Preferences,uid:str=Depends(current_user_id)):
    return _call(c.preferences,uid,channel,body.alerts,body.approvals)


@router.post('/messaging/{channel}/test')
def test(channel:Channel,uid:str=Depends(current_user_id)):
    return _call(service.test_message,uid,channel)


@router.delete('/messaging/{channel}')
def disconnect(channel:Channel,uid:str=Depends(current_user_id)):
    return _call(c.disconnect,uid,channel)


class Proposal(BaseModel):
    ticket_id:str=Field(min_length=1,max_length=80)


@router.post('/messaging/proposals/paper')
def propose(body:Proposal,uid:str=Depends(current_user_id)):
    from apps.api.messaging import actions
    return _call(actions.propose,uid,body.ticket_id)
