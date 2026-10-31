import csv
import io

import pytest


NSE_FIELDS = ['SYMBOL', 'NAME OF COMPANY', ' SERIES', ' DATE OF LISTING', ' ISIN NUMBER']
SME_FIELDS = ['SYMBOL', 'NAME_OF_COMPANY', 'SERIES', 'DATE_OF_LISTING', 'ISIN_NUMBER', '']
BSE_FIELDS = ['FinInstrmId', 'TckrSymb', 'FinInstrmNm', 'ISIN', 'SctyTpFlg', 'FinInstrmTp', 'Sts', 'SctySrs', 'Xchg']
KITE_FIELDS = ['instrument_token', 'exchange_token', 'tradingsymbol', 'name', 'segment', 'exchange', 'instrument_type']
RELIANCE = 'INE002A01018'
TCS = 'INE467B01029'


def catalog(fields, rows=()):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(fields)
    writer.writerows(rows)
    return buffer.getvalue()


def kite(exchange, symbol, token, code, *, segment=None):
    return [token, code, symbol, 'provider name', segment or exchange, exchange, 'EQ']


def build(nse=(), sme=(), bse=(), kn=(), kb=()):
    from apps.research.market_universe import build_universe
    return build_universe(catalog(NSE_FIELDS, nse), catalog(SME_FIELDS, sme),
                          catalog(BSE_FIELDS, bse), catalog(KITE_FIELDS, kn), catalog(KITE_FIELDS, kb))


def test_same_isin_groups_venues_without_merging_their_series():
    result = build(nse=[['RELIANCE', 'Reliance Industries', 'EQ', '29-NOV-1995', RELIANCE]],
                   bse=[['500325', 'RELIANCE', 'Reliance Industries', RELIANCE, 'EQ', 'E', 'A', 'A', 'BSE']],
                   kn=[kite('NSE', 'RELIANCE', 100, 1)], kb=[kite('BSE', 'RELIANCE', 200, 500325)])
    entries = {e['key']: e for e in result['entries']}
    assert set(entries) == {'NSE:RELIANCE', 'BSE:500325'}
    assert entries['NSE:RELIANCE']['listing_date'] == '1995-11-29'
    assert entries['NSE:RELIANCE']['yahoo_symbol'] == 'RELIANCE.NS'
    assert entries['BSE:500325']['yahoo_symbol'] == 'RELIANCE.BO'
    assert entries['BSE:500325']['kite_token'] == 200
    assert result['by_isin'][RELIANCE] == ['BSE:500325', 'NSE:RELIANCE']
    assert result['counts']['unique_isins'] == 1
    assert result['counts']['entries'] == 2


def test_sme_headers_and_current_series_suffix_are_normalized():
    result = build(sme=[['RAVITA', 'Ravita Engineering', 'ST', '22-Oct-26', 'INE1UEL01023', '']],
                   kn=[kite('NSE', 'RAVITA-ST', 300, 3), kite('NSE', 'RAVITA-SM', 301, 4)])
    entry, = result['entries']
    assert entry['key'] == 'NSE:RAVITA'
    assert entry['kite_symbol'] == 'RAVITA-ST'
    assert entry['kite_token'] == 300
    assert entry['listing_date'] == '2026-10-22'
    assert entry['yahoo_symbol'] == 'RAVITA.NS'


def test_bse_flags_are_not_enough_to_admit_preferences_rights_or_trust_units():
    instruments = [('710057', 'VALIANTOCPS', 'INE565V03016'),
                   ('751148', 'SHIVAM-RE1', 'INE637H20024'),
                   ('540526', 'IRBINVIT', 'INE183W23014'),
                   ('542602', 'EMBASSY', 'INE041025011'),
                   ('570004', 'JISLDVREQS', 'IN9175A01010')]
    result = build(bse=[[code, symbol, symbol, isin, 'EQ', 'E', 'A', 'B', 'BSE']
                        for code, symbol, isin in instruments],
                   kb=[kite('BSE', symbol, i+10, code) for i, (code, symbol, _) in enumerate(instruments)])
    assert result['entries'] == []
    assert len(result['exclusions']) + len(result['uncertain']) >= 5


def test_classification_uses_fields_not_words_in_company_names():
    result = build(bse=[['543971', 'BONDADA', 'Bondada Engineering', 'INE0Q8P01029', 'EQ', 'E', 'A', 'B', 'BSE'],
                        ['532985', 'SENSEX1', 'Kotak Mutual Fund', 'INF174KA1JD7', 'MF   ', 'E', 'A', 'B', 'BSE'],
                        ['500325', 'RELIANCE', 'Reliance debt', RELIANCE, 'DB', 'D', 'A', 'F', 'BSE'],
                        ['532540', 'TCS', 'TCS', TCS, 'EQ', 'E', 'S', 'A', 'BSE']],
                   kb=[kite('BSE', 'BONDADA', 10, 543971)])
    assert [e['symbol'] for e in result['entries']] == ['BONDADA']
    assert len(result['exclusions']) == 3


def test_bse_provider_identity_requires_exact_code_and_symbol():
    result = build(bse=[['500325', 'RELIANCE', 'Reliance', RELIANCE, 'EQ', 'E', 'A', 'A', 'BSE']],
                   kb=[kite('BSE', 'WRONG', 10, 500325), kite('BSE', 'RELIANCE', 11, 999999)])
    entry, = result['entries']
    assert entry['kite_token'] is None
    assert entry['yahoo_symbol'] is None
    assert result['identity_conflicts']


def test_wrong_kite_segment_and_wrong_current_series_never_match():
    result = build(nse=[['TCS', 'TCS', 'BE', '25-AUG-2004', TCS]],
                   kn=[kite('NSE', 'TCS', 1, 1, segment='INDICES'), kite('NSE', 'TCS-SM', 2, 2)])
    entry, = result['entries']
    assert entry['kite_token'] is None
    assert entry['yahoo_symbol'] == 'TCS.NS'
    assert any(r['reason'] == 'kite_instrument_unmatched' for r in result['uncertain'])


def test_shadow_aliases_and_bad_isins_are_explicitly_uncertain():
    result = build(bse=[['100325', 'RELIANCE#', 'Reliance alias', RELIANCE, 'EQ', 'E', 'A', 'A', 'BSE'],
                        ['500325', 'RELIANCE', 'Reliance', 'NA', 'EQ', 'E', 'A', 'A', 'BSE']],
                   nse=[['TCS', 'TCS', 'EQ', '', 'INE467B01020']])
    assert result['entries'] == []
    assert len(result['uncertain']) == 3
    assert result['counts']['uncertain'] == 3


def test_conflicting_official_identity_is_reported_and_not_chosen():
    result = build(nse=[['RELIANCE', 'Reliance', 'EQ', '', RELIANCE],
                        ['RELIANCE', 'Different security', 'EQ', '', TCS]],
                   kn=[kite('NSE', 'RELIANCE', 1, 1)])
    assert result['entries'] == []
    assert result['identity_conflicts'][0]['key'] == 'NSE:RELIANCE'


def test_duplicate_rows_deduplicate_but_two_provider_tokens_are_conflict():
    row = ['TCS', 'TCS', 'EQ', '', TCS]
    result = build(nse=[row, row], kn=[kite('NSE', 'TCS', 1, 1), kite('NSE', 'TCS', 2, 2)])
    entry, = result['entries']
    assert entry['kite_token'] is None
    assert result['identity_conflicts']
    assert len(result['sources']['nse_csv']['sha256']) == 64


def test_missing_required_schema_is_a_hard_failure():
    from apps.research.market_universe import build_universe
    with pytest.raises(ValueError, match='schema'):
        build_universe('SYMBOL\nTCS\n', catalog(SME_FIELDS), catalog(BSE_FIELDS),
                       catalog(KITE_FIELDS), catalog(KITE_FIELDS))


def test_unique_normal_bse_id_can_be_a_yahoo_candidate_without_kite():
    result = build(bse=[['500325', 'RELIANCE', 'Reliance', RELIANCE, 'EQ', 'E', 'A', 'A', 'BSE']])
    entry, = result['entries']
    assert entry['kite_token'] is None
    assert entry['yahoo_symbol'] == 'RELIANCE.BO'
    assert 'chart_metadata_validation_required' in entry['yahoo_mapping_status']


def test_duplicate_bse_security_ids_never_share_a_yahoo_series():
    result = build(bse=[['500325', 'DUPLICATE', 'Reliance', RELIANCE, 'EQ', 'E', 'A', 'A', 'BSE'],
                        ['532540', 'DUPLICATE', 'TCS', TCS, 'EQ', 'E', 'A', 'A', 'BSE']])
    assert len(result['entries']) == 2
    assert all(e['yahoo_symbol'] is None for e in result['entries'])
    assert any(c['reason'] == 'ambiguous_bse_security_id' for c in result['identity_conflicts'])


def test_bse_inactive_and_unknown_types_have_explicit_reasons():
    result = build(bse=[['500325', 'RELIANCE', 'Reliance', RELIANCE, 'EQ', 'E', 'I', 'A', 'BSE'],
                        ['532540', 'TCS', 'TCS', TCS, '', 'E', 'A', 'A', 'BSE']])
    assert result['entries'] == []
    assert result['counts']['exclusions_by_reason']['bse_inactive'] == 1
    assert result['counts']['uncertain_by_reason']['bse_security_type_uncertain'] == 1


def test_public_catalog_helper_has_finite_sources_and_retains_raw_artifacts():
    import zipfile
    from apps.research.market_universe import CATALOG_URLS, fetch_catalogs
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, 'w') as archive:
        archive.writestr('SCRIP/BSE_EQ_SCRIP_30102026.csv', catalog(BSE_FIELDS))
    contents = {url: (zipped.getvalue() if name == 'bse_zip' else b'raw-public-catalog')
                for name, url in CATALOG_URLS.items()}
    requested = []
    def opener(request, *, timeout):
        requested.append(request.full_url)
        assert timeout == 25
        return io.BytesIO(contents[request.full_url])
    fetched = fetch_catalogs(opener=opener)
    assert set(requested) == set(CATALOG_URLS.values())
    assert set(fetched['inputs']) == {'nse_csv', 'sme_csv', 'bse_csv', 'kite_nse_csv', 'kite_bse_csv'}
    assert fetched['inputs']['bse_csv'] == catalog(BSE_FIELDS).encode()
    assert fetched['artifacts']['bse_zip'] == zipped.getvalue()
    assert fetched['metadata']['bse_csv']['member'] == 'SCRIP/BSE_EQ_SCRIP_30102026.csv'


def test_public_catalog_helper_rejects_oversized_response():
    from apps.research.market_universe import MAX_BYTES, fetch_catalogs
    with pytest.raises(ValueError, match='size'):
        fetch_catalogs(opener=lambda request, **kwargs: io.BytesIO(b'x'*(MAX_BYTES+1)))
