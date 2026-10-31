"""Offline multi-year comparisons using the existing Trade Desk replay engine."""
from __future__ import annotations

import csv
import json
import statistics
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from pydantic import model_validator

from apps.api.desk import bars_source, paper
from apps.api.desk.bars_source import BarSeries
from apps.api.desk.profile import DeskProfile
from apps.api.kite import backtest, rest, strategies
from .archive import ArchiveError, ArchiveIntegrityError, IST


class ResearchBody(backtest.BacktestBody):
    """Research-only bound. The public BacktestBody retains its60-day guard."""
    @model_validator(mode='after')
    def _dates(self):
        if self.end_date < self.start_date or (self.end_date-self.start_date).days+1 > 731:
            raise ValueError('research replay requires1–731 calendar days')
        if self.split_date is not None and not self.start_date < self.split_date <= self.end_date:
            raise ValueError('invalid research split date')
        return self


def choose_symbols(daily, start, *, limit=20, min_sessions=40):
    """Pre-period liquidity only; no strategy outcome or future price ranks it."""
    scores = []
    for sym, rows in daily.items():
        past = [r for r in rows if datetime.fromisoformat(r[0]).astimezone(IST).date() < start]
        past = sorted(past, key=lambda r: r[0])[-60:]
        values = [float(r[4])*float(r[5]) for r in past if float(r[5]) > 0]
        if len(values) >= min_sessions:
            scores.append((-statistics.median(values), sym))
    return [s for _, s in sorted(scores)[:limit]]


def variants(symbols):
    configs = [('orb15-mid-2r',15,'or_mid',None), ('orb30-mid-2r',30,'or_mid',None),
               ('orb15-low-2r',15,'or_low',None), ('orb15-mid-volume-2r',15,'or_mid',1.5)]
    out = []
    for ident, minutes, stop, volume in configs:
        s = strategies.Strategy(name=ident, symbols=symbols, mode='off', interval='5m',
             setup='orb', or_minutes=minutes, stop_rule=stop, target_r=2,
             min_volume_x=volume, order_type='MARKET', max_signals_per_day=3).doc()
        out.append(s | {'id':'research-'+ident, 'version':1})
    return out


def write_json(path, value, *, create_only=False):
    import os
    temporary = path.with_suffix(path.suffix+'.tmp')
    try:
        with temporary.open('w') as f:
            os.chmod(temporary, 0o600)
            json.dump(value, f, allow_nan=False, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        if create_only:
            try:os.link(temporary,path)
            except FileExistsError as exc:
                raise ArchiveIntegrityError('refusing to overwrite saved historical metadata') from exc
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def regime_summary(trades, profile, start, end):
    """Period slices of the same continuous-account trades, including quiet months."""
    months, quarters = {}, {}
    at = start.replace(day=1)
    while at <= end:
        month = at.strftime('%Y-%m')
        quarter = f'{at.year}-Q{(at.month-1)//3+1}'
        months[month] = paper.stats([t for t in trades if t['trade_date'][:7] == month], profile)
        quarters.setdefault(quarter, [])
        quarters[quarter].extend(t for t in trades if t['trade_date'][:7] == month)
        at = (at.replace(day=28)+timedelta(days=4)).replace(day=1)
    return dict(months=months, quarters={k:paper.stats(v,profile) for k,v in quarters.items()})


def run_comparison(archive):
    config = archive.config
    start, end = date.fromisoformat(config['start']), date.fromisoformat(config['end'])
    selected = archive.get_meta('research_symbols')
    if not selected:
        raise ArchiveError('research universe has not been frozen')
    owner = config['owner']
    series = {}
    for sym in selected:
        raw = archive.load(owner, sym, '5m')
        rows = [(datetime.fromisoformat(r[0]), *r[1:6]) for r in raw]
        series[sym] = BarSeries(sym, '5m', rows, 'archive:kite', rows[-1][0].isoformat() if rows else None)

    def archived(uid, sym, interval='5m', **kwargs):
        if uid != owner:
            raise ArchiveError('research owner mismatch')
        if interval != '5m' or sym not in series:
            raise ArchiveError('offline archive series unavailable')
        return series[sym]

    profile = DeskProfile(**config['profile'])
    results = []
    with patch.object(bars_source, 'get_history_rows', archived), patch.object(
        rest.KiteClient, '_call', side_effect=ArchiveError('broker I/O forbidden during offline replay')):
        for s in variants(selected):
            body = ResearchBody(strategy_id=s['id'], start_date=start, end_date=end,
                                split_date=end-timedelta(days=182), symbols=selected)
            for stress in (False, True):
                p = profile if not stress else DeskProfile(**(profile.model_dump() | {
                    'slippage_ticks':min(50,max(profile.slippage_ticks+2,profile.slippage_ticks*2))}))
                result = backtest.run(owner, s, body, datetime.now(timezone.utc), p)
                result['regimes'] = regime_summary(result['trades'], p, start, end)
                label = s['id']+('-stress' if stress else '')
                result['research'] = dict(archive_source_version=config['engine_version'],
                    owner_scope='private', higher_slippage_stress=stress,
                    slippage_ticks=p.slippage_ticks, selection='pre-period60-session median traded value',
                    slippage_increased=p.slippage_ticks > profile.slippage_ticks,
                    survivorship_bias='Current eligible NIFTY500; delisted/renamed/missing constituents are not reconstructed.',
                    execution='Offline replay; no broker calls, paper-ledger writes or enabled strategies.')
                write_json(archive.root/f'{label}.json', result)
                results.append(dict(id=label, stress=stress, summary=result['summary'],
                                    costs=result['costs'], coverage=result['coverage']))
                archive.set_meta('research_completed_variants', len(results))
    csv_path = archive.root/'comparison.csv'
    with csv_path.open('w', newline='') as f:
        import os
        os.chmod(csv_path, 0o600)
        columns = ['variant','period','trades','win_rate','pnl_net','charges','profit_factor','max_drawdown']
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for r in results:
            for period, metrics in r['summary'].items():
                writer.writerow(dict(variant=r['id'], period=period,
                    **{k:metrics.get(k) for k in columns[2:]}))
    write_json(archive.root/'comparison.json', dict(start=str(start),end=str(end),
               split=str(end-timedelta(days=182)),symbols=selected,results=results,
               note='Predefined research comparisons, not an optimization winner or evidence of future profitability.'))
    lines = ['# Historical strategy research', '', f'Period: {start} to {end}. Held-out boundary: {end-timedelta(days=182)}.',
             '', 'Universe: '+', '.join(selected), '',
             'Symbols were selected only by pre-period median traded value from the frozen current NIFTY500 universe.',
             'Current-universe survivorship and provider-adjusted/corporate-action limitations remain. Missing sessions are skipped, never filled.',
             'The existing exchange calendar only enumerates2026 holidays; earlier exchange closures can appear as no-data coverage days.',
             'The four variants were specified before inspecting outcomes. No strategy was enabled. Fills/charges/slippage are estimates.',
             '', 'Detailed continuous-account results, coverage, train/test and trade legs are in the private JSON files.', '']
    for r in results:
        lines += ['## '+r['id'], '', '```json', json.dumps(r['summary'],indent=2,allow_nan=False),'```','']
    report = archive.root/'comparison.md'
    report.write_text('\n'.join(lines)); report.chmod(0o600)
    archive.set_meta('research_complete', True)
    return results
