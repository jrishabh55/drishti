"""Pure reconciliation of current official NSE/BSE security catalogs.

This module neither fetches candles nor resolves names approximately. ISINs
identify securities, not permanent corporate identities or historical listings.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime


CATALOG_URLS = {
    'nse_csv': 'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv',
    'sme_csv': 'https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv',
    'bse_zip': 'https://www.bseindia.com/downloads/Help/file/scrip.zip',
    'kite_nse_csv': 'https://api.kite.trade/instruments/NSE',
    'kite_bse_csv': 'https://api.kite.trade/instruments/BSE',
}
NSE_SERIES = frozenset(('EQ', 'BE', 'BZ', 'SM', 'ST', 'SZ'))
SYMBOL = re.compile(r'[A-Z0-9][A-Z0-9&._-]{0,39}\Z')
MAX_BYTES = 32 * 1024**2
MAX_ROWS = 100000


def fetch_catalogs(*, opener=urllib.request.urlopen):
    """Fetch five finite public reference files, retaining raw data for freezing.

    Returns ``inputs`` for ``build_universe(**inputs)``, binary ``artifacts``
    for caller-owned persistence, and JSON-safe artifact ``metadata``. No login,
    credentials, prices, alternate URLs, or filesystem writes are involved.
    """
    artifacts, metadata = {}, {}
    for name, url in CATALOG_URLS.items():
        request = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bseindia.com/'})
        with opener(request, timeout=25) as response:
            data = response.read(MAX_BYTES+1)
        if len(data) > MAX_BYTES:
            raise ValueError('public catalog exceeds size bound')
        artifacts[name] = data
        metadata[name] = dict(url=url, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    with zipfile.ZipFile(io.BytesIO(artifacts['bse_zip'])) as archive:
        members = [m for m in archive.infolist()
                   if re.fullmatch(r'(?:SCRIP/)?BSE_EQ_SCRIP_[0-9]{8}\.csv', m.filename, re.IGNORECASE)]
        if len(members) != 1:
            raise ValueError('BSE archive has ambiguous security master members')
        member = members[0]
        if member.file_size > MAX_BYTES:
            raise ValueError('BSE security master exceeds size bound')
        artifacts['bse_csv'] = archive.read(member)
    data = artifacts['bse_csv']
    metadata['bse_csv'] = dict(url=CATALOG_URLS['bse_zip'], member=member.filename,
                              bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    inputs = {name: data for name, data in artifacts.items() if name != 'bse_zip'}
    return dict(inputs=inputs, artifacts=artifacts, metadata=metadata)


def _header(value):
    return ' '.join(value.strip().upper().replace('_', ' ').split())


def _read_csv(value, required):
    raw = value.encode('utf-8') if isinstance(value, str) else bytes(value)
    if len(raw) > MAX_BYTES:
        raise ValueError('catalog exceeds size bound')
    reader = csv.reader(io.StringIO(raw.decode('utf-8-sig')))
    fields = [_header(f) for f in next(reader, [])]
    named = [f for f in fields if f]
    if len(set(named)) != len(named) or not set(required) <= set(fields):
        raise ValueError('catalog schema is missing or ambiguous')
    rows = []
    for values in reader:
        if not values or not any(v.strip() for v in values):
            continue
        if len(values) != len(fields):
            raise ValueError('catalog schema row width mismatch')
        rows.append({f: v.strip() for f, v in zip(fields, values) if f})
        if len(rows) > MAX_ROWS:
            raise ValueError('catalog exceeds row bound')
    return rows, dict(rows=len(rows), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def _isin_valid(value):
    if not re.fullmatch(r'IN[A-Z0-9]{9}[0-9]', value):
        return False
    digits = ''.join(c if c.isdigit() else str(ord(c)-55) for c in value)
    total = sum((2*int(c)//10 + 2*int(c)%10) if i % 2 else int(c)
                for i, c in enumerate(reversed(digits)))
    return total % 10 == 0


def _ordinary_isin(value):
    if not _isin_valid(value):
        return 'uncertain', 'missing_or_invalid_isin'
    # NSDL's company issuer prefix plus equity security code. IN9 denotes
    # different rights and is kept aside rather than called ordinary equity.
    if value.startswith('IN9'):
        return 'uncertain', 'different_rights_or_part_paid_isin'
    if not value.startswith('INE') or value[7:9] != '01':
        return 'excluded', 'non_ordinary_isin_security_code'
    return None


def _listing_date(value):
    if not value:
        return None
    for pattern in ('%d-%b-%Y', '%d-%b-%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _positive_int(value):
    return int(value) if re.fullmatch('[0-9]+', value or '') and int(value) > 0 else None


def build_universe(nse_csv, sme_csv, bse_csv, kite_nse_csv, kite_bse_csv):
    """Build a bounded snapshot; unknown identities remain explicitly unresolved.

    Inputs are raw CSV strings/bytes. For BSE pass the unmodified
    ``BSE_EQ_SCRIP_DDMMYYYY.csv`` member of the official SCRIP.ZIP, whose
    bundled format documents security flags, trading status and instrument type.
    ``entries`` retain exchange-specific keys. ``by_isin`` groups the same
    security across venues; no historical prices or exchange series are merged.
    """
    inputs = dict(nse_csv=nse_csv, sme_csv=sme_csv, bse_csv=bse_csv,
                  kite_nse_csv=kite_nse_csv, kite_bse_csv=kite_bse_csv)
    nse_required = ('SYMBOL', 'NAME OF COMPANY', 'SERIES', 'DATE OF LISTING', 'ISIN NUMBER')
    required = dict(nse_csv=nse_required, sme_csv=nse_required,
                    bse_csv=('FININSTRMID', 'TCKRSYMB', 'FININSTRMNM', 'ISIN', 'SCTYTPFLG',
                             'FININSTRMTP', 'STS', 'SCTYSRS', 'XCHG'))
    kite_required = ('INSTRUMENT TOKEN', 'EXCHANGE TOKEN', 'TRADINGSYMBOL', 'SEGMENT', 'EXCHANGE', 'INSTRUMENT TYPE')
    required.update(kite_nse_csv=kite_required, kite_bse_csv=kite_required)
    catalogs, sources = {}, {}
    for name, raw in inputs.items():
        catalogs[name], sources[name] = _read_csv(raw, required[name])
    exclusions, uncertain, conflicts = [], [], []
    entries, blocked = {}, set()

    def record(destination, source, key, reason, **details):
        destination.append(dict(source=source, key=key, reason=reason, **details))

    def add(entry, source):
        key = entry['key']
        if key in blocked:
            return
        existing = entries.get(key)
        identity = ('exchange', 'symbol', 'security_code', 'isin', 'series')
        if existing and any(existing.get(f) != entry.get(f) for f in identity):
            conflicts.append(dict(key=key, reason='official_identity_conflict',
                                  identities=[{f: e.get(f) for f in identity} for e in (existing, entry)]))
            entries.pop(key)
            blocked.add(key)
        elif existing:
            existing['source_catalogs'] = sorted(set(existing['source_catalogs']+[source]))
        else:
            entries[key] = entry | dict(source_catalogs=[source], kite_token=None, kite_symbol=None,
                                        yahoo_symbol=None, classification='ordinary_equity_candidate')

    for source in ('nse_csv', 'sme_csv'):
        for row in catalogs[source]:
            symbol, series, isin = row['SYMBOL'].upper(), row['SERIES'].upper(), row['ISIN NUMBER'].upper()
            key = 'NSE:'+symbol
            if series not in NSE_SERIES:
                record(exclusions, source, key, 'unsupported_nse_series', series=series, isin=isin)
                continue
            classification = _ordinary_isin(isin)
            if classification:
                kind, reason = classification
                record(uncertain if kind == 'uncertain' else exclusions, source, key, reason, isin=isin)
                continue
            if not SYMBOL.fullmatch(symbol) or not row['NAME OF COMPANY']:
                record(uncertain, source, key, 'unverified_security_symbol_or_name', isin=isin)
                continue
            listed = _listing_date(row['DATE OF LISTING'])
            if row['DATE OF LISTING'] and listed is None:
                record(uncertain, source, key, 'invalid_listing_date', value=row['DATE OF LISTING'])
            add(dict(key=key, exchange='NSE', symbol=symbol, security_code=None,
                     name=row['NAME OF COMPANY'], isin=isin, series=series, listing_date=listed), source)

    for row in catalogs['bse_csv']:
        source, code = 'bse_csv', row['FININSTRMID']
        key = 'BSE:'+code
        symbol, isin = row['TCKRSYMB'].upper(), row['ISIN'].upper()
        if row['XCHG'].upper() != 'BSE':
            record(uncertain, source, key, 'bse_exchange_mismatch', exchange=row['XCHG'], isin=isin)
            continue
        status = row['STS'].upper()
        if status != 'A':
            reason = {'S': 'bse_suspended', 'I': 'bse_inactive'}.get(status)
            record(exclusions if reason else uncertain, source, key, reason or 'bse_status_uncertain', status=status, isin=isin)
            continue
        if row['SCTYTPFLG'].upper() not in ('EQ', 'MF', 'DB', 'GS') or row['FININSTRMTP'].upper() not in ('E', 'D', 'C'):
            record(uncertain, source, key, 'bse_security_type_uncertain',
                   security_type=row['SCTYTPFLG'], instrument_type=row['FININSTRMTP'], isin=isin)
            continue
        if row['SCTYTPFLG'].upper() != 'EQ' or row['FININSTRMTP'].upper() != 'E':
            record(exclusions, source, key, 'bse_not_equity_instrument',
                   security_type=row['SCTYTPFLG'], instrument_type=row['FININSTRMTP'], isin=isin)
            continue
        classification = _ordinary_isin(isin)
        if classification:
            kind, reason = classification
            record(uncertain if kind == 'uncertain' else exclusions, source, key, reason, isin=isin)
            continue
        if not _positive_int(code) or not SYMBOL.fullmatch(symbol) or not row['FININSTRMNM']:
            record(uncertain, source, key, 'unverified_bse_security_id', symbol=symbol, isin=isin)
            continue
        add(dict(key=key, exchange='BSE', symbol=symbol, security_code=code,
                 name=row['FININSTRMNM'], isin=isin, series=row['SCTYSRS'].upper(), listing_date=None), source)

    kite_rows = defaultdict(list)
    for source, exchange in (('kite_nse_csv', 'NSE'), ('kite_bse_csv', 'BSE')):
        for row in catalogs[source]:
            symbol = row['TRADINGSYMBOL'].upper()
            if (row['EXCHANGE'].upper() != exchange or row['SEGMENT'].upper() != exchange
                    or row['INSTRUMENT TYPE'].upper() != 'EQ'):
                record(exclusions, source, exchange+':'+symbol, 'kite_wrong_segment_or_type')
                continue
            token = _positive_int(row['INSTRUMENT TOKEN'])
            code = _positive_int(row['EXCHANGE TOKEN'])
            if token is None or code is None:
                record(uncertain, source, exchange+':'+symbol, 'invalid_kite_instrument_identity')
                continue
            kite_rows[exchange].append(dict(symbol=symbol, token=token, code=str(code)))

    used = set()
    by_symbol, by_code = defaultdict(list), defaultdict(list)
    for exchange, rows in kite_rows.items():
        for row in rows:
            by_symbol[exchange, row['symbol']].append(row)
            by_code[exchange, row['code']].append(row)
    for entry in entries.values():
        exchange, symbol, key = entry['exchange'], entry['symbol'], entry['key']
        if exchange == 'NSE':
            names = {symbol, symbol+'-'+entry['series']}
            matches = [r for name in sorted(names) for r in by_symbol[exchange, name]]
            entry['yahoo_symbol'] = symbol+'.NS'
        else:
            entry['yahoo_symbol'] = symbol+'.BO'
            same_code = by_code[exchange, entry['security_code']]
            matches = [r for r in same_code if r['symbol'] == symbol]
            if any(r['symbol'] != symbol for r in same_code):
                conflicts.append(dict(key=key, reason='kite_security_code_symbol_conflict',
                                      official_symbol=symbol, provider_symbols=sorted({r['symbol'] for r in same_code})))
                matches = []
                entry['yahoo_symbol'] = None
        unique = {(r['token'], r['symbol'], r['code']): r for r in matches}
        if len(unique) == 1:
            matched = next(iter(unique.values()))
            entry.update(kite_token=matched['token'], kite_symbol=matched['symbol'])
            used.add((exchange, matched['token'], matched['symbol'], matched['code']))
            if exchange == 'BSE':
                entry['yahoo_symbol'] = symbol+'.BO'
        elif len(unique) > 1:
            conflicts.append(dict(key=key, reason='kite_instrument_identity_conflict',
                                  tokens=sorted({r['token'] for r in matches})))
            if exchange == 'BSE':
                entry['yahoo_symbol'] = None
        else:
            record(uncertain, 'kite_'+exchange.lower()+'_csv', key, 'kite_instrument_unmatched', isin=entry['isin'])
        if exchange == 'BSE' and entry['yahoo_symbol'] is None:
            entry['yahoo_mapping_status'] = 'unverified'
        else:
            entry['yahoo_mapping_status'] = 'official_exchange_symbol_suffix; chart_metadata_validation_required'

    bse_symbols = defaultdict(list)
    for entry in entries.values():
        if entry['exchange'] == 'BSE':
            bse_symbols[entry['symbol']].append(entry)
    for symbol, duplicates in bse_symbols.items():
        if len(duplicates) > 1:
            conflicts.append(dict(reason='ambiguous_bse_security_id', symbol=symbol,
                                  keys=sorted(e['key'] for e in duplicates)))
            for entry in duplicates:
                entry.update(yahoo_symbol=None, yahoo_mapping_status='unverified')

    # A broker token cannot identify two different saved series.
    token_entries = defaultdict(list)
    for entry in entries.values():
        if entry['kite_token'] is not None:
            token_entries[entry['kite_token']].append(entry)
    for token, duplicates in token_entries.items():
        if len(duplicates) > 1:
            conflicts.append(dict(reason='kite_token_reused_for_distinct_series', token=token,
                                  keys=sorted(e['key'] for e in duplicates)))
            for entry in duplicates:
                entry.update(kite_token=None, kite_symbol=None)
                if entry['exchange'] == 'BSE':
                    entry.update(yahoo_symbol=None, yahoo_mapping_status='unverified')

    values = sorted(entries.values(), key=lambda e: e['key'])
    by_isin = defaultdict(list)
    for entry in values:
        by_isin[entry['isin']].append(entry['key'])
    for exchange, rows in kite_rows.items():
        for row in rows:
            if (exchange, row['token'], row['symbol'], row['code']) not in used:
                # Broker instruments alone never extend the official stock universe.
                record(exclusions, 'kite_'+exchange.lower()+'_csv', exchange+':'+row['symbol'],
                       'kite_not_selected_by_official_catalog', security_code=row['code'])
    return dict(schema=1, entries=values, by_isin=dict(sorted(by_isin.items())),
        counts=dict(entries=len(values), unique_isins=len(by_isin), by_exchange=dict(Counter(e['exchange'] for e in values)),
                    mapped_kite=sum(e['kite_token'] is not None for e in values),
                    mapped_yahoo=sum(e['yahoo_symbol'] is not None for e in values),
                    exclusions=len(exclusions), uncertain=len(uncertain), identity_conflicts=len(conflicts),
                    exclusions_by_reason=dict(Counter(r['reason'] for r in exclusions)),
                    uncertain_by_reason=dict(Counter(r['reason'] for r in uncertain))),
        sources=sources, exclusions=exclusions, uncertain=uncertain, identity_conflicts=conflicts,
        policy=dict(nse_series=sorted(NSE_SERIES), bse_flags=dict(SctyTpFlg='EQ', FinInstrmTp='E', Sts='A'),
                    isin='valid checksum; INE company prefix; equity security code01',
                    documentation=['https://nsdl.co.in/downloadables/Core%20Services.pdf', CATALOG_URLS['bse_zip']],
                    scope='Current official NSE/BSE conservative ordinary-equity candidates; not a historical all-market reconstruction.',
                    same_isin='Groups the same security across venues; never merges candle series or different ISINs.'))
