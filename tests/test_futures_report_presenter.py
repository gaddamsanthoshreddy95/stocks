from src.presenter.futures_report import FuturesReportPresenter


def test_explanation_reports_exact_band_discount_and_concrete_numbers():
    item = {'symbol': 'TEST', 'technical_score': 80, 'final_action': 'REJECT', 'futures_selection': {
        'checks': {
            'valuation_quality': {'factors': {'stock_pe': 10, 'sector_pe': 20,
                                             'minimum_allowed_pe': 19, 'maximum_allowed_pe': 21}},
            'debt_free_quality': {'factors': {'total_debt': 150, 'debt_to_equity': .2}},
            'delivery_quality': {'factors': {'delivery_percent': 30, 'monthly_delivery_percent': 40}},
        }}}
    rendered = FuturesReportPresenter.render({'reviewed': [item], 'today_news': None})
    assert '10.00 versus sector 20.00' in rendered
    assert '-50.00%' in rendered
    assert '19.00–21.00' in rendered
    assert '₹150.00 crore' in rendered
    assert '30.00% versus 40.00%' in rendered
    assert 'model sentiment flag alone' in rendered


def test_range_uses_52_weeks_and_rejects_short_history():
    import pandas as pd
    history = pd.DataFrame({'High': [500, 200, 180], 'Low': [1, 100, 120]},
                           index=pd.to_datetime(['2025-01-01', '2025-10-10', '2026-10-08']))
    result = FuturesReportPresenter.price_range(history, 150)
    assert result['high'] == 200
    assert result['low'] == 100
    assert result['change_from_high_percent'] == -25
    assert result['rise_from_low_percent'] == 50
    assert 'high' not in FuturesReportPresenter.price_range(history.iloc[-1:], 150)


def test_report_displays_rsi_news_and_distinguishes_review_from_approval():
    item = {'symbol': 'TEST', 'final_action': 'REJECT', 'discovery_reason': 'High relative volume',
            'technical': {'rsi': 36.9, 'trend': 'STRONG BULLISH'},
            'news': {'news_state': 'ANALYZED', 'headlines': [
                {'title': 'Results scheduled', 'url': 'https://example.com/results',
                 'source': 'Company', 'published': '2026-10-08'}]}}
    rendered = FuturesReportPresenter.render({'reviewed': [item]})
    assert '36.90 (Weak)' in rendered
    assert 'Why reviewed: High relative volume' in rendered
    assert '[Results scheduled](https://example.com/results)' in rendered
    assert 'Approved: 0' in rendered
    assert '52-week high / low: ₹Not verified' in rendered
