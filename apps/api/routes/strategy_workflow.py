"""Owned research/scanner selections become reviewable, OFF strategy drafts."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.auth import current_user_id
from apps.api.desk import bars, portfolios
from apps.api.desk.store import get_desk_store
from apps.api.jobs.store import get_store
from apps.api.scanner.store import get_scanner_store

router = APIRouter()
_DRAFT_TTL = timedelta(minutes=30)
_SCHEMA = '''CREATE TABLE IF NOT EXISTS desk_strategy_drafts (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, doc TEXT NOT NULL);'''


def _now():
    return datetime.now(timezone.utc)


class DraftBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str | None = Field(None, min_length=1, max_length=100)
    r_multiple: float | None = Field(None, ge=1, le=10)
    scanner_id: str | None = Field(None, min_length=1, max_length=100)
    symbols: list[str] = Field(default_factory=list, max_length=20)
    portfolio_id: str = Field('default', pattern=portfolios.ID_PATTERN)

    @model_validator(mode='before')
    @classmethod
    def finite_json(cls, value):
        # FastAPI's default validation-error renderer cannot serialize NaN in
        # typed-field inputs. Refuse it before producing a Pydantic error.
        try:
            json.dumps(value, allow_nan=False)
        except (ValueError, TypeError):
            raise HTTPException(422, 'Draft values must be finite JSON values') from None
        return value

    @field_validator('symbols')
    @classmethod
    def normalize(cls, values):
        try:
            return sorted({bars.nse_symbol(s) for s in values})
        except bars.BarsError as exc:
            raise ValueError(str(exc)) from None

    @model_validator(mode='after')
    def source(self):
        if self.r_multiple is not None and not self.run_id:
            raise ValueError('A target multiple requires an analysis run')
        if self.run_id and (self.scanner_id or self.symbols):
            raise ValueError('Choose one research run, a scanner, or selected symbols')
        if not (self.run_id or self.scanner_id or self.symbols):
            raise ValueError('Choose research, a scanner, or at least one NSE symbol')
        return self


class SaveBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    strategy: dict[str, Any]



def _db():
    return get_desk_store()._conn()


def _ensure():
    with _db() as c:
        c.executescript(_SCHEMA)


def _owned(did, uid):
    _ensure()
    with _db() as c:
        row = c.execute('SELECT doc FROM desk_strategy_drafts WHERE id=? AND user_id=?',
                        (did, uid)).fetchone()
    if row is None:
        raise HTTPException(404, 'strategy draft not found')
    return json.loads(row['doc'])


def _put(doc, uid):
    with _db() as c:
        c.execute('INSERT INTO desk_strategy_drafts VALUES (?,?,?) ON CONFLICT(id) '
                  'DO UPDATE SET doc=excluded.doc WHERE user_id=excluded.user_id',
                  (doc['id'], uid, json.dumps(doc)))


def _run_levels(run_id, capital, uid, r_multiple=2):
    from apps.api.routes.runs import get_run_levels
    return get_run_levels(run_id, capital=capital, risk_pct=1, r_multiple=r_multiple, user_id=uid).model_dump()


def _scan(sid, uid):
    from apps.api.routes.scanners import run_scanner
    return run_scanner(sid, uid)


def _template(symbols, pid, name):
    return dict(name=name[:80], mode='off', symbols=symbols, portfolio_id=pid,
                interval='5m', setup='orb', level=None, or_minutes=15,
                stop_rule='or_mid', stop_param=None, target_r=2, order_type='LIMIT',
                max_signals_per_day=1, confirm_ttl_s=120)


@router.post('/desk/strategy-drafts', status_code=201)
def create_draft(body: DraftBody, user_id: str = Depends(current_user_id)):
    portfolio = portfolios.get(user_id, body.portfolio_id)
    if portfolio is None:
        raise HTTPException(404, 'portfolio not found')
    warnings = ['A draft does not place trades. Save it Off, review today’s data and limits, '
                'then explicitly choose Paper in Strategies.']
    if body.run_id:
        run = get_store().get_run(body.run_id)
        if run is None or (run.user_id or 'anonymous') != user_id:
            raise HTTPException(404, 'run not found')
        if run.status != 'completed':
            raise HTTPException(409, 'Research must complete before making a strategy draft')
        if not run.ticker.upper().endswith('.NS'):
            raise HTTPException(422, 'Strategy automation currently supports NSE cash stocks (.NS)')
        sym = bars.nse_symbol(run.ticker)
        response = _run_levels(body.run_id, portfolio['capital'], user_id, r_multiple=body.r_multiple or 2)
        levels = response.get('levels')
        if not response.get('viable') or response.get('currency') != 'INR' or not levels:
            raise HTTPException(422, 'Research has no viable long-side INR levels for this draft')
        entry, stop, target = (float(levels[k]['price']) for k in ('entry', 'stop', 'target'))
        if not all(math.isfinite(v) for v in (entry, stop, target)) or not 0 < stop < entry < target:
            raise HTTPException(422, 'Research levels must have a positive stop below entry and target above entry')
        strategy = _template([sym], body.portfolio_id, f'Research {sym}')
        strategy.update(setup='level', level=entry, stop_rule='price',
                        stop_param=stop, target_rule='price', target_param=target,
                        target_r=round((target-entry)/(entry-stop), 6))
        source = dict(kind='analysis', run_id=body.run_id, ticker=run.ticker,
                      analysis_date=run.analysis_date, entry=entry, stop=stop, target=target,
                      r_multiple=body.r_multiple or 2)
        warnings.append(f'These levels came from research dated {run.analysis_date}; this is an '
                        'intraday rule draft, not the original swing horizon. Recheck the levels and exit horizon before enabling it.')
    elif body.scanner_id:
        scan = get_scanner_store().get_scanner(body.scanner_id)
        if scan is None or (not scan['prebuilt'] and scan['user_id'] != user_id):
            raise HTTPException(404, 'scanner not found')
        result = _scan(body.scanner_id, user_id)
        matches = {m['symbol'] for m in result['matches']}
        ordered = list(dict.fromkeys(m['symbol'] for m in result['matches']))
        syms = body.symbols or ordered[:20]
        if not syms or not set(syms) <= matches:
            raise HTTPException(422, 'Choose up to 20 stocks matching this scanner now')
        strategy = _template(syms if body.symbols else [], body.portfolio_id, scan['name'])
        if not body.symbols:
            strategy['scanner_id'] = body.scanner_id
        source = dict(kind='scanner', scanner_id=body.scanner_id, preview_symbols=syms,
                      selection='snapshot' if body.symbols else 'dynamic',
                      data_as_of=result.get('data_as_of'), source=result.get('source', 'delayed:yahoo'))
        warnings.append('Selected symbols are a fixed snapshot.' if body.symbols else
                        'This follows the saved scanner definition, selecting up to 20 current matches. Preview names are not pinned; temporary scanner-page edits and display filters are not included.')
    else:
        strategy = _template(body.symbols, body.portfolio_id, 'Paper experiment')
        source = dict(kind='symbols', symbols=body.symbols)
    now = _now()
    doc = dict(id=uuid.uuid4().hex, state='review', created_at=now.isoformat(),
               expires_at=(now+_DRAFT_TTL).isoformat(), source=source, strategy=strategy,
               warnings=warnings, saved_strategy_id=None)
    _ensure()
    _put(doc, user_id)
    return doc


@router.get('/desk/strategy-drafts/{did}')
def get_draft(did: str, user_id: str = Depends(current_user_id)):
    return _owned(did, user_id)


@router.post('/desk/strategy-drafts/{did}/save', status_code=201)
def save_draft(did: str, body: SaveBody, user_id: str = Depends(current_user_id)):
    from apps.api.kite import execution
    from apps.api.kite.store import get_kite_store
    from apps.api.routes.kite import create_strategy, _valid

    if body.strategy.get('mode', 'off') != 'off':
        raise HTTPException(409, 'Save the reviewed strategy Off; enable Paper separately in Strategies')
    try:
        encoded = json.dumps(body.strategy, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        raise HTTPException(422, 'Strategy values must be finite JSON values') from None
    fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
    with execution.user_lock(user_id):
        doc = _owned(did, user_id)
        if doc.get('saved_strategy_id'):
            if doc.get('save_fingerprint') != fingerprint:
                raise HTTPException(409, 'This draft was already saved with different rules; edit the strategy')
            strategy = get_kite_store().get_strategy(doc['saved_strategy_id'], user_id)
            if strategy is None:
                raise HTTPException(409, 'The saved strategy was deleted; create a new draft')
            return dict(draft=doc, strategy=strategy)
        if datetime.fromisoformat(doc['expires_at']) <= _now():
            raise HTTPException(409, 'This draft expired; create a new preview to review current inputs')
        if doc['state'] != 'review':
            raise HTTPException(409, 'Draft creation was interrupted; check Strategies before making another draft')
        valid = _valid({**body.strategy, 'mode':'off'}, user_id)
        # Claim before creating the OFF record. An interrupted request is never
        # blindly retried into a duplicate strategy, even across API processes.
        with _db() as c:
            claimed = dict(doc, state='creating', save_fingerprint=fingerprint)
            count = c.execute('UPDATE desk_strategy_drafts SET doc=? WHERE id=? AND user_id=? AND doc=?',
                              (json.dumps(claimed), did, user_id, json.dumps(doc))).rowcount
        if count != 1:
            raise HTTPException(409, 'This draft is being saved; refresh it')
        strategy = create_strategy(valid, user_id)
        doc.update(state='saved', save_fingerprint=fingerprint, saved_strategy_id=strategy['id'],
                   reviewed_strategy=body.strategy, reviewed_at=_now().isoformat())
        _put(doc, user_id)
        return dict(draft=doc, strategy=strategy)


@router.get('/desk/readiness')
def readiness(request: Request, user_id: str = Depends(current_user_id)):
    from apps.api.kite.store import get_kite_store
    from apps.api.routes.kite import status

    kite = status(user_id)
    loops = {}
    for name, task in getattr(request.app.state, 'trading_loops', {}).items():
        loops[name] = ('disabled' if task is None else 'running' if not task.done()
                       else 'stopped' if task.cancelled() or task.exception() is None else 'failed')
    strategies = get_kite_store().list_strategies(user_id)
    blockers = []
    access = kite.get('data_access') or {}
    if access.get('session') != 'active':
        blockers.append('Connect your own or a directly shared Kite data source and complete the daily login')
    stream = access.get('live') or {}
    ready_symbols = [s for s in stream.get('symbols', []) if str(s.get('source', '')).startswith('live:')]
    if stream.get('state') != 'live' or not ready_symbols:
        blockers.append('The live stream has no fresh, backfilled symbols; delayed data may miss the strategy freshness window')
    if loops.get('paper') != 'running':
        state = loops.get('paper', 'not started')
        blockers.append(f'Paper settlement background processing is {state}; restore the paper worker to advance fills and exits without an open page')
    if loops.get('strategies') != 'running':
        blockers.append('Strategy background processing is not running in this API process')
    if not any(s.get('mode') == 'paper' for s in strategies):
        blockers.append('No paper strategy is enabled')
    from apps.api.scanner.calendar import is_market_open
    return dict(kite=kite, loops=loops, market_open=is_market_open(_now()),
                data_coverage=dict(subscribed=len(stream.get('symbols', [])), ready=len(ready_symbols)),
                strategies=[dict(id=s['id'], name=s['name'], mode=s['mode'],
                                 portfolio_id=s.get('portfolio_id','default'),
                                 last_scan=s.get('last_scan')) for s in strategies],
                paper_blockers=blockers,
                live_orders_enabled=bool(kite['orders']['server_enabled']),
                validation_note='Connection readiness and controlled tests do not establish a completed real-market paper trial.')
