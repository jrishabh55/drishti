"""Persist each user's watched-symbol entry setup independently of the list."""
import json
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from apps.api.auth import current_user_id
from apps.api.desk.bars import nse_symbol, BarsError
from apps.api.desk.store import get_desk_store
from apps.api.routes.desk import SafeErrorsRoute
router=APIRouter(route_class=SafeErrorsRoute)
_SCHEMA='''CREATE TABLE IF NOT EXISTS desk_watch_rules (
 user_id TEXT NOT NULL, symbol TEXT NOT NULL, doc TEXT NOT NULL, PRIMARY KEY(user_id,symbol))'''

class WatchRule(BaseModel):
    model_config=ConfigDict(allow_inf_nan=False)
    setup: Literal['orb','level']='orb'
    level: float | None=Field(None,gt=0)
    interval: Literal['1m','5m']='5m'
    min_volume_x: float | None=Field(None,gt=0,le=100)
    @model_validator(mode='after')
    def valid_level(self):
        if self.setup=='level' and self.level is None:
            raise ValueError('A custom level setup needs a positive level price')
        return self

@router.get('/desk/watch-rules')
def read(user_id: str=Depends(current_user_id)):
    with get_desk_store()._conn() as conn:
        conn.execute(_SCHEMA)
        rows=conn.execute('SELECT symbol,doc FROM desk_watch_rules WHERE user_id=?',(user_id,)).fetchall()
    out={}
    for row in rows:
        try:  # a rule saved before non-finite levels were refused is skipped, not a 500 for every rule
            out[row['symbol']]=WatchRule.model_validate(json.loads(row['doc'])).model_dump()
        except (ValueError, ValidationError):
            continue
    return out

@router.put('/desk/watch-rules/{symbol}')
def save(symbol: str, body:WatchRule, user_id:str=Depends(current_user_id)):
    try:
        symbol=nse_symbol(symbol)
    except BarsError as exc:
        raise HTTPException(422,str(exc)) from exc
    doc=body.model_dump()
    with get_desk_store()._conn() as conn:
        conn.execute(_SCHEMA)
        conn.execute('INSERT OR REPLACE INTO desk_watch_rules VALUES (?,?,?)',
                     (user_id,symbol,json.dumps(doc)))
    return doc
