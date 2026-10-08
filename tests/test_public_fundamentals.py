from datetime import date, timedelta
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from src.quality.engine import CandidateQualityEngine
from src.quality.models import FundamentalSnapshot
from src.quality.public_fundamentals import PublicFundamentalProvider, number, yoy_growth


TODAY = date(2026, 10, 8)


def section(identifier, periods, rows):
    headers = '<th></th>' + ''.join(f'<th>{period}</th>' for period in periods)
    body = ''.join('<tr><td>' + key + '</td>' + ''.join(f'<td>{value}</td>' for value in values)
                   + '</tr>' for key, values in rows.items())
    return f'<section id="{identifier}"><table><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></section>'


def screener_html(debt=0):
    ratios = ''.join(f'<li><span class="name">{key}</span><span class="number">{value}</span></li>'
                     for key, value in [('Stock P/E', 25), ('ROE', 20), ('ROCE', 25)])
    return '<ul id="top-ratios">' + ratios + '</ul>' + section(
        'quarters', ['Dec 2024', 'Mar 2025', 'Jun 2025', 'Sep 2025', 'Dec 2025', 'Mar 2026', 'Jun 2026'],
        {'Sales +': [100, 100, 100, 100, 110, 120, 130], 'Net Profit +': [10, 10, 10, 10, 11, 12, 13]}) + section(
        'balance-sheet', ['Mar 2025', 'Mar 2026'],
        {'Borrowings +': [0, debt], 'Equity Capital': [10, 10], 'Reserves': [90, 90]}) + section(
        'shareholding', ['Dec 2025', 'Mar 2026', 'Jun 2026'],
        {'Promoters +': [45, 45, 45], 'FIIs +': [8, 9, 10], 'DIIs +': [7, 7, 8]})


def test_screener_preserves_reporting_dates_and_calculates_three_quarters_yoy():
    snapshot, _ = PublicFundamentalProvider.parse_screener('TCS', screener_html(), today=TODAY)
    assert snapshot.quarterly_revenue_growth_pct == (10, 20, 30)
    assert snapshot.quarterly_profit_growth_pct == (10, 20, 30)
    assert snapshot.total_debt == 0
    assert snapshot.debt_to_equity == 0
    assert snapshot.fii_holding_change_pct_points == 1
    assert snapshot.promoter_holding_percent == 45
    assert snapshot.evidence['quarterly_revenue_growth_pct']['period'] == '2026-06-30'


def test_empty_consolidated_template_is_rejected_so_standalone_can_be_used():
    with pytest.raises(ValueError, match='ratios are absent'):
        PublicFundamentalProvider.parse_screener('ICICIGI',
            '<ul id="top-ratios"><li><span class="name">Stock P/E</span><span class="number"></span></li></ul>', today=TODAY)


def test_screener_does_not_treat_old_or_future_filings_as_current():
    stale, _ = PublicFundamentalProvider.parse_screener('TCS', screener_html(), today=date(2028, 10, 8))
    assert stale.total_debt is None
    assert stale.fii_holding_percent is None
    assert stale.quarterly_profit_growth_pct is None
    assert stale.roe is None
    future, _ = PublicFundamentalProvider.parse_screener('TCS', screener_html(), today=date(2025, 1, 1))
    assert future.fii_holding_percent is None


def test_missing_zero_promoter_row_uses_matching_dates_only():
    html = screener_html().replace('<tr><td>Promoters +</td><td>45</td><td>45</td><td>45</td></tr>', '')
    annual = section('extra', ['Mar 2025', 'Mar 2026', 'Jun 2026'], {'Promoters +': [0, 0, 0]})
    extra_table = annual[annual.index('<table>'):annual.index('</table>') + len('</table>')]
    # Append the second table inside the shareholding section.
    html = html[:-len('</section>')] + extra_table + '</section>'
    snap, _ = PublicFundamentalProvider.parse_screener('COFORGE', html, today=TODAY)
    assert snap.promoter_holding_percent == 0
    assert snap.promoter_holding_change_pct_points == 0
    prefix, holding_html = html.split('<section id="shareholding">', 1)
    wrong = prefix + '<section id="shareholding">' + holding_html.replace(
        '<th>Jun 2026</th>', '<th>Jun 2025</th>', 1)
    # A different latest quarterly period must not be filled from the annual row.
    mismatched, _ = PublicFundamentalProvider.parse_screener('COFORGE', wrong, today=TODAY)
    assert mismatched.promoter_holding_percent is None


def test_yoy_matches_same_quarter_not_previous_row_and_rejects_missing_base():
    assert yoy_growth(['Dec 2025', 'Mar 2026', 'Jun 2026'], [100, 120, 130]) is None
    assert number('--') is None
    assert number('nan') is None
    assert number('1,000.50%') == 1000.5


def test_moneycontrol_lookup_requires_exact_nse_symbol():
    rows = [
        {'pdt_dis_nm': 'Wrong<span>INE123, BHARATFORG, 123</span>',
         'link_src': 'https://www.moneycontrol.com/india/stockpricequote/wrong'},
        {'pdt_dis_nm': 'Coforge<span>INE456, COFORGE, 456</span>',
         'link_src': 'https://www.moneycontrol.com/india/stockpricequote/coforge'}]
    assert PublicFundamentalProvider.moneycontrol_url('COFORGE', rows).endswith('/coforge')
    assert PublicFundamentalProvider.moneycontrol_url('OTHER', rows) is None


def test_moneycontrol_pairs_pe_and_sector_and_distinguishes_deal_dates():
    html = '<span class="nsepe">22</span><td class="nsesc_ttm">20</td><td class="nsed20ad">49.36</td>'
    old = html + '<div id="lockdl"><div class="bd_bx"><div class="br_date">24 Jun, 2026</div></div></div>'
    values, dates = PublicFundamentalProvider.parse_moneycontrol(old, today=TODAY)
    assert values['pe_ratio'] == 22
    assert values['sector_pe'] == 20
    assert values['monthly_delivery_percent'] == 49.36
    assert values['block_deal_price_impact'] is False
    recent = old.replace('24 Jun, 2026', '07 Oct, 2026')
    assert PublicFundamentalProvider.parse_moneycontrol(recent, today=TODAY)[0]['block_deal_price_impact'] is True
    assert 'block_deal_price_impact' not in PublicFundamentalProvider.parse_moneycontrol(html, today=TODAY)[0]


def test_delivery_baseline_is_volume_weighted_and_excludes_latest_session():
    provider = PublicFundamentalProvider(today=TODAY)
    records = []
    for i in range(21):
        delivered, traded = (90, 100) if i == 0 else (10, 100) if i % 2 else (100, 200)
        frame = pd.DataFrame({'DELIV_QTY': [delivered], 'TTL_TRD_QNTY': [traded]}, index=['TCS'])
        records.append((TODAY - timedelta(days=i + 1), frame, f'https://example.test/report{i}.csv'))
    provider._archives = records
    values, evidence = provider._delivery('TCS')
    assert values['delivery_percent'] == 90
    assert values['monthly_delivery_percent'] == pytest.approx(1100 / 3000 * 100)
    assert evidence['monthly_delivery_percent']['sessions'] == 20
    assert records[0][2] not in evidence['monthly_delivery_percent']['report_urls']


def test_reported_debt_overrides_rounded_zero_debt_ratio():
    snapshot = FundamentalSnapshot('TCS', debt_to_equity=0, total_debt=0.01)
    engine = CandidateQualityEngine(fundamental_provider=SimpleNamespace(get_fundamentals=lambda symbol: snapshot))
    scores = engine.stock_selection_quality('TCS', sector_one_year_return=1, stock_one_year_return=2, news=None)
    assert scores['debt_free_quality'].status == 'FAIL'


def test_financial_company_singular_borrowing_row_is_not_missing():
    snap, _ = PublicFundamentalProvider.parse_screener(
        'PNBHOUSING', screener_html(debt=71199).replace('Borrowings +', 'Borrowing'), today=TODAY)
    assert snap.total_debt == 71199


def test_known_weak_ownership_fails_even_when_pledge_field_is_unavailable():
    snap = FundamentalSnapshot('PNBHOUSING', promoter_holding_percent=28)
    engine = CandidateQualityEngine(fundamental_provider=SimpleNamespace(get_fundamentals=lambda symbol: snap))
    scores = engine.stock_selection_quality('PNBHOUSING', sector_one_year_return=1, stock_one_year_return=2, news=None)
    assert scores['promoter_holding_quality'].status == 'FAIL'


def test_annual_history_extends_one_calendar_year_with_too_few_trading_sessions():
    from src.data_provider.kite_data_provider import KiteDataProvider
    provider = KiteDataProvider.__new__(KiteDataProvider)
    full = pd.DataFrame({'Close': range(400)}, index=pd.date_range('2025-01-01', periods=400))
    recent = full.iloc[-245:]
    provider.get_data = lambda symbol: recent
    called = []
    def long_history(symbol, period):
        called.append((symbol, period))
        return full
    provider.get_long_history = long_history
    history = provider.get_annual_history('NIFTY IT')
    assert len(history) == 400
    assert called == [('NIFTY IT', '2y')]


def test_commentary_is_documented_interpretation_and_negative_guidance_blocks_strong_rating():
    text = 'Strong growth momentum. Robust demand and strong pipeline. Strong order book. Margin expansion. Confident strong outlook.'
    strength, evidence = PublicFundamentalProvider.commentary(text)
    assert strength == 'VERY_STRONG'
    assert 'rule-based' in evidence['method']
    assert PublicFundamentalProvider.commentary(text + ' We lowered our guidance due to weak demand.')[0] != 'VERY_STRONG'


def test_source_failure_keeps_other_public_fields_and_is_recorded(tmp_path):
    def fetch(url):
        if 'screener.in' in url:
            return screener_html().encode()
        if 'autosuggestion' in url:
            return json.dumps([{'pdt_dis_nm': 'TCS<span>INE1, TCS, 1</span>',
                                'link_src': 'https://www.moneycontrol.com/india/stockpricequote/tcs'}]).encode()
        if 'moneycontrol' in url:
            return b'<span class="nsepe">25</span><span class="nsesc_ttm">25</span>'
        raise ValueError('Unavailable source')
    provider = PublicFundamentalProvider(today=TODAY, fetcher=fetch, cache_directory=tmp_path)
    provider._archives = []
    snap = provider.get_fundamentals('TCS')
    assert snap.roe == 20
    assert snap.pe_ratio == snap.sector_pe == 25
    assert snap.promoter_pledge is None
    assert 'NSE promoter pledge' in snap.evidence['source_errors']
    assert provider.get_fundamentals('TCS') is snap
