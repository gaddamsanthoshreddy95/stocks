from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.futures.config import FuturesScanConfig
from src.futures.research import CompanyResearch
from src.futures.scanner import short_priority
from src.futures.scoring import directional_setup, prepare
from src.quality.config import QualityConfig
from src.quality.engine import CandidateQualityEngine
from src.quality.futures_selection import REQUIRED_CHECKS
from src.quality.models import FundamentalSnapshot, QualityScore
from src.quality.public_fundamentals import PublicFundamentalProvider
from test_bidirectional_futures import scanner_fixture, candles, NOW
from test_public_fundamentals import screener_html, section


def company(**updates):
    snapshot = FundamentalSnapshot('TEST', pe_ratio=25, sector_pe=25, total_debt=0, debt_to_equity=0,
        roe=20, roce=25, fii_holding_percent=10, dii_holding_percent=10,
        fii_holding_change_pct_points=1, dii_holding_change_pct_points=1,
        promoter_holding_percent=50, promoter_holding_change_pct_points=0, promoter_pledge=0,
        delivery_percent=50, monthly_delivery_percent=40,
        quarterly_revenue_growth_pct=(4, 5, 6), quarterly_profit_growth_pct=(4, 5, 6),
        commentary_strength='VERY_STRONG', block_deal_price_impact=False)
    return replace(snapshot, **updates)


def research(snapshot, side='SHORT', sector='IT', config=None):
    provider = SimpleNamespace(get_fundamentals=lambda symbol: snapshot)
    service = CompanyResearch(config or QualityConfig(), provider)
    stock = candles(100+np.arange(260)*.2)
    sector_history = candles(100+np.arange(260)*.1)
    news = {'news_state': 'NO_RELEVANT_NEWS', 'checked_at': NOW.isoformat()}
    vwap = QualityScore(100, 'PASS', factors={'current_price': 200, 'vwap': 199})
    return service.assess('TEST', sector, side, news=news, stock_history=stock,
                          sector_history=sector_history, session_vwap=vwap), service, stock, sector_history, news, vwap


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_shared_research_preserves_every_existing_check_and_configured_threshold(side):
    config = replace(QualityConfig(), minimum_roe_percent=22, maximum_sector_pe_deviation=.03)
    result, service, stock, sector, news, vwap = research(company(), side, config=config)
    from src.quality.engine import _returns
    original = service.engine.stock_selection_quality('TEST', sector_one_year_return=_returns(sector, (252,))[252],
        stock_one_year_return=_returns(stock, (252,))[252], news=news)
    original.update({'valuation_quality': service.engine.valuation_quality('TEST'),
                     'delivery_quality': service.engine.delivery_quality('TEST'), 'vwap_quality': vwap})
    assert set(result['checks']) == set(REQUIRED_CHECKS)
    assert result['checks'] == {key: original[key].to_dict() for key in REQUIRED_CHECKS}
    assert result['thresholds']['minimum_roe_percent'] == 22
    assert result['checks']['roe_quality']['status'] == 'FAIL'
    assert not result['eligible']


def test_overvaluation_selling_and_negative_growth_support_short_but_rules_remain_fail():
    result, *_ = research(company(pe_ratio=50, total_debt=100, previous_total_debt=50,
        fii_holding_change_pct_points=-1, quarterly_profit_growth_pct=(5, -2, -4)))
    interpretations = {row['condition']: row for row in result['short_interpretation']}
    for field in ('valuation', 'debt_trend', 'fii', 'profit_growth'):
        assert interpretations[field]['short_thesis'] == 'SUPPORTS_SHORT'
    for rule in ('valuation_quality', 'debt_free_quality', 'institutional_holding_quality', 'quarterly_results_quality'):
        assert result['checks'][rule]['status'] == 'FAIL'
    assert result['policy_review_required']
    assert not result['eligible']
    assert interpretations['delivery']['short_thesis'] == 'NEUTRAL'


@pytest.mark.parametrize('sector', ['BANKING', 'NBFC'])
def test_lending_sector_context_keeps_debt_and_roce_rules_without_inventing_asset_quality(sector):
    result, *_ = research(company(total_debt=1000, debt_to_equity=4, roce=8), sector=sector)
    assert result['checks']['debt_free_quality']['status'] == 'FAIL'
    assert result['checks']['roce_quality']['status'] == 'FAIL'
    assert 'Asset quality' in result['sector_interpretation']
    assert any(flag['check'] == 'debt_free_quality' for flag in result['review_flags'])
    interpretation = {row['condition']: row for row in result['short_interpretation']}
    assert interpretation['debt_trend']['short_thesis'] == 'SECTOR_REVIEW'


def test_high_debt_alone_does_not_invent_a_rising_debt_trend():
    result, *_ = research(company(total_debt=100))
    debt = next(row for row in result['short_interpretation'] if row['condition'] == 'debt_trend')
    assert debt['short_thesis'] == 'UNKNOWN'
    assert debt['values']['previous_total_debt'] is None


def test_prior_debt_comes_from_the_previous_reported_period():
    snapshot, _ = PublicFundamentalProvider.parse_screener('TEST', screener_html(50), today=date(2026, 10, 8))
    assert snapshot.total_debt == 50
    assert snapshot.previous_total_debt == 0
    assert snapshot.previous_debt_to_equity == 0
    assert snapshot.evidence['previous_total_debt']['period'] == '2025-03-31'


@pytest.mark.parametrize('today,expected', [(date(2026, 10, 8), 20), (date(2025, 1, 1), None)])
def test_previous_profitability_uses_published_periods_without_future_filings(today, expected):
    html = screener_html() + section('ratios', ['Mar 2025', 'Mar 2026'], {'ROCE %': [20, 25], 'ROE %': [15, 20]})
    snapshot, _ = PublicFundamentalProvider.parse_screener('TEST', html, today=today)
    assert snapshot.previous_roce == expected
    if expected is not None:
        assert snapshot.previous_roe == 15
        assert snapshot.evidence['previous_roce']['period'] == '2025-03-31'


def test_all_discovered_candidates_receive_research_and_news_independent_of_review_budget():
    scanner, _ = scanner_fixture()
    scanner.config = replace(scanner.config, review_per_direction=1)
    scanner.news_provider = Mock(return_value={'news_state': 'NO_RELEVANT_NEWS', 'checked_at': NOW.isoformat()})
    report = scanner.scan(now=NOW, include_backtest=False)
    assert scanner.news_provider.call_count == report['universe_size']
    assert scanner.company_research.engine.fundamental_provider.get_fundamentals.call_count == report['universe_size']
    for item in report['reviewed']:
        assert set(item['company_research']['checks']) == set(REQUIRED_CHECKS)
        assert 'news' in item
    assert report['approved_count'] == 0


def prepared_bear():
    values = 110-np.arange(220)*.02
    frame = candles(values)
    frame['Open'] = values+.08
    frame['High'] = values+.09
    frame['Low'] = values-.04
    for period, offset in [(9, .01), (21, .02), (50, .04), (200, .06)]:
        frame[f'EMA{period}'] = values+offset
    frame['RSI'], frame['ADX'], frame['ATR'] = 40., 30., 2.
    frame['PLUS_DI'], frame['MINUS_DI'] = 10., 30.
    frame['MACD'], frame['MACD_SIGNAL'], frame['MACD_HISTOGRAM'] = -.2, -.1, -.1
    frame['RVOL'] = 1.5
    return frame


def test_early_bearish_continuation_is_visible_before_a_large_fall():
    frame = prepared_bear()
    result = directional_setup(frame, 'SHORT', prepared=True)
    assert result['setup_type'] == 'BEARISH_TREND_CONTINUATION'
    assert result['timing'] == 'READY'
    assert result['confirmed']
    assert abs(result['evidence']['move_percent']) < .1
    assert result['evidence']['short_trigger_price'] == frame.Low.iloc[-1]


def test_fresh_breakdown_has_a_precise_support_trigger_and_does_not_relabel_old_breaks():
    frame = prepared_bear()
    frame.iloc[-1, frame.columns.get_loc('Close')] -= .1
    frame.iloc[-1, frame.columns.get_loc('Low')] -= .1
    result = directional_setup(frame, 'SHORT', prepared=True)
    assert result['setup_type'] == 'SUPPORT_BREAKDOWN'
    assert result['evidence']['fresh_support_breakdown']
    assert result['evidence']['short_trigger_price'] == pytest.approx(frame.Low.iloc[-21:-1].min())
    extended = frame.copy()
    for i in range(len(extended)-5, len(extended)):
        previous_low = float(extended.Low.iloc[i-1])
        extended.iloc[i, extended.columns.get_loc('Close')] = previous_low-.02
        extended.iloc[i, extended.columns.get_loc('Open')] = previous_low+.02
        extended.iloc[i, extended.columns.get_loc('High')] = previous_low+.03
        extended.iloc[i, extended.columns.get_loc('Low')] = previous_low-.03
    result = directional_setup(extended, 'SHORT', prepared=True)
    assert not result['evidence']['fresh_support_breakdown']
    assert result['evidence']['breakdown_age_bars'] >= 2


def test_excessively_oversold_stock_is_not_a_timely_short_even_with_high_score():
    frame = prepared_bear()
    frame['RSI'] = 20
    result = directional_setup(frame, 'SHORT', prepared=True)
    assert result['timing'] == 'TOO LATE'
    assert 'SHORT_EXCESSIVELY_OVERSOLD' in result['reason_codes']


def test_pullback_rejection_requires_an_actual_recovery_then_rejection():
    frame = prepared_bear()
    frame.iloc[-2, frame.columns.get_loc('Close')] += .05
    result = directional_setup(frame, 'SHORT', prepared=True)
    assert result['setup_type'] == 'PULLBACK_REJECTION'
    assert result['evidence']['pullback_rejection_confirmed']
    assert result['evidence']['short_trigger_price'] == frame.Low.iloc[-1]


def test_morning_volume_reuses_existing_elapsed_session_calculation():
    frame = candles(np.full(220, 200))
    frame['IS_LIVE_CANDLE'] = False
    frame['LIVE_SESSION_PROGRESS'] = 1.
    frame.loc[frame.index[-1], 'IS_LIVE_CANDLE'] = True
    frame.loc[frame.index[-1], 'LIVE_SESSION_PROGRESS'] = .1
    frame.loc[frame.index[-1], 'Volume'] = 20
    result = prepare(frame)
    assert result.RVOL.iloc[-1] == pytest.approx(2)
    assert result.RVOL_BASIS.iloc[-1] == 'EXISTING_ELAPSED_SESSION_VOLUME_PIPELINE'


def test_fresh_setups_are_prioritized_over_generic_bearish_rank():
    assert short_priority({'timing': 'READY', 'evidence': {'fresh_support_breakdown': True}}) > short_priority(
        {'timing': 'READY', 'setup_type': 'BEARISH_TREND_CONTINUATION', 'confirmed': True})


def test_report_shows_actual_research_values_original_statuses_and_review_flags():
    from src.presenter.futures_opportunities import FuturesOpportunitiesPresenter
    company_research, *_ = research(company(pe_ratio=50, total_debt=100, previous_total_debt=50))
    item = {'symbol': 'TEST', 'side': 'SHORT', 'sector': 'IT', 'final_decision': 'WAIT',
            'setup_type': 'BEARISH_TREND_CONTINUATION', 'technical_score': 80, 'BULLISH_SCORE': 20,
            'BEARISH_SCORE': 80, 'company_research': company_research, 'evidence': {'short_trigger_price': 200}}
    text = FuturesOpportunitiesPresenter.candidate(item)
    assert 'valuation_quality | FAIL' in text
    assert "'stock_pe': 50" in text and "'sector_pe': 25" in text
    assert 'institutional_holding_quality' in text and 'delivery_quality' in text
    assert 'Policy review required:' in text
    assert 'SUPPORTS_SHORT' in text
    assert 'trigger ₹200.0000' in text


@pytest.mark.parametrize('case,expected', [
    ('valid', 'APPROVED'), ('trigger_not_crossed', 'WAIT'), ('too_far', 'TOO LATE'),
    ('no_confirmation', 'WAIT'), ('research_unknown', 'UNKNOWN'), ('policy_conflict', 'WAIT'),
    ('insufficient_downside', 'REJECT'), ('stop_invalid', 'REJECT'),
])
def test_short_approval_requires_research_trigger_confirmation_remaining_space_and_stop(case, expected):
    from src.quality.futures_execution import CHECKS, check
    scanner, _ = scanner_fixture()
    from futures_mode_fixture import SimulatedProvider, SyntheticNoCostModel
    scanner.costs = SyntheticNoCostModel()
    research_result = {'unavailable_checks': [], 'failed_checks': [], 'policy_review_required': False}
    if case == 'research_unknown':
        research_result['unavailable_checks'] = ['roe_quality']
    if case == 'policy_conflict':
        research_result.update(failed_checks=['quarterly_results_quality'], policy_review_required=True)
    item = {'symbol': 'TEST', 'side': 'SHORT', 'technical_score': 95,
            'setup_type': 'BEARISH_TREND_CONTINUATION', 'reason_codes': [], 'company_research': research_result}
    trigger = 199.9 if case == 'trigger_not_crossed' else 201 if case == 'too_far' else 200
    evidence = {'support': 199.8 if case == 'insufficient_downside' else 190,
                'resistance': 210, 'short_trigger_price': trigger,
                'setup_invalidation_price': 201 if case == 'stop_invalid' else 200.1}
    setup = {'technical_score': 95, 'confirmed': case != 'no_confirmation', 'timing': 'READY',
             'reason_codes': [], 'setup_type': 'BEARISH_TREND_CONTINUATION', 'evidence': evidence}
    checks = {key: check(True, {}, 'VALIDATED') for key in CHECKS}
    checks['futures_oi_quality'] = check(True, {'price_change_percent': -1}, 'SHORT_BUILDUP')
    data = {'daily': candles(np.full(260, 200)), 'intraday': SimulatedProvider(['TEST']).intraday,
            'quote': {'timestamp':NOW.isoformat(), 'last_price':200, 'depth': {'buy': [{'price': 200, 'quantity': 10000}],
                               'sell': [{'price': 200.02, 'quantity': 10000}]}}}
    with patch('src.futures.scanner.assess_execution', return_value=checks), patch('src.futures.scanner.directional_setup', return_value=setup):
        scanner._execution(item, data, {'lot_size': 500}, (None, None),
            {'news_state': 'NO_RELEVANT_NEWS', 'checked_at': NOW.isoformat()}, {}, NOW, None)
    assert item['final_decision'] == expected
    assert item['short_entry']['trigger_price'] == trigger
    assert item['plan']['target'] == pytest.approx(199.4)
    assert item['plan']['stop_loss'] == pytest.approx(200.4)
    assert item['short_entry']['remaining_downside_percent'] == pytest.approx((200-evidence['support'])/200*100)
