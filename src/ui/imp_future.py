"""Manual full-universe Futures discount research; shared scanner gates remain authoritative."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from math import isfinite

import pandas as pd

from src.ui.ai_stock import AiStockHistory, scan_ai_stock, render as render_theme


class ImpFutureHistory(AiStockHistory):
    table = 'imp_future_scan_runs'


def discount_evidence(item):
    """Compare paired quotes captured for the exact evaluated contract, never daily closes."""
    sample = item.get('entry_quality_input') or {}
    quote = sample.get('quote') or {}
    contract = item.get('contract') or {}
    result = {'status': 'UNAVAILABLE', 'reason': 'PAIRED_QUOTES_UNAVAILABLE'}
    try:
        spot, future = float(sample['stock_price']), float(quote['last_price'])
        if not all(isfinite(x) and x > 0 for x in (spot, future)):
            return {**result, 'reason': 'INVALID_PRICE'}
        if (sample.get('symbol') != item['symbol'] or not contract.get('instrument_token')
                or str(quote.get('instrument_token')) != str(contract['instrument_token'])
                or (sample.get('contract') or {}).get('tradingsymbol') != contract.get('tradingsymbol')):
            return {**result, 'reason': 'CONTRACT_IDENTITY_UNVERIFIED'}
        at = pd.Timestamp(sample['decision_at'])
        stamps = [pd.Timestamp(sample['stock_quote_at']), pd.Timestamp(quote['timestamp'])]
        # Kite's exchange timestamps are naive IST; never interpret them as UTC.
        stamps = [t.tz_localize('Asia/Kolkata') if t.tzinfo is None else t for t in stamps]
        if at.tzinfo is None or any(t.tzinfo is None or not 0 <= (at-t).total_seconds() <= 30 for t in stamps):
            return {**result, 'reason': 'PAIRED_QUOTES_STALE_OR_UNVERIFIED'}
        # Decimal comparison includes exactly 1% without rounding smaller discounts up.
        qualifies = Decimal(str(future)) <= Decimal(str(spot)) * Decimal('0.99')
        return {'status': 'MATCH' if qualifies else 'NO_MATCH',
                'reason': 'FUTURES_DISCOUNT_AT_LEAST_1_PERCENT' if qualifies else 'FUTURES_DISCOUNT_BELOW_1_PERCENT',
                'stock_price': spot, 'futures_price': future,
                'discount_percent': (spot-future)/spot*100,
                'stock_quote_at': str(stamps[0]), 'futures_quote_at': str(stamps[1]),
                'contract': contract['tradingsymbol']}
    except (KeyError, TypeError, ValueError, OverflowError):
        return result


def filter_discount_report(report, limit):
    result = deepcopy(report)
    rows = result.get('reviewed', [])
    for item in rows:
        item['futures_discount'] = discount_evidence(item)
    matches = {(i['symbol'], i['side']) for i in rows if i['futures_discount']['status'] == 'MATCH'}
    result['discount_summary'] = {'rule': 'Futures price <= 99% of stock price (minimum 1% verified discount)',
        'minimum_discount_percent': 1,
        'directional_evaluations': len(rows), 'statuses': dict(Counter(i['futures_discount']['status'] for i in rows)),
        'matching_stocks': len({symbol for symbol, _ in matches}),
        'note': 'Discount is a price filter, not entry approval. Unavailable evidence is not a confirmed non-match.'}
    # Preserve the original order and scores. Filter the full ranked lists before applying UI limits.
    evidence = {(i['symbol'], i['side']): i['futures_discount'] for i in rows}
    for key in ('report_a', 'report_b', 'report_c'):
        selected = [i for i in result.get(key, []) if (i['symbol'], i['side']) in matches]
        for item in selected:
            item['futures_discount'] = evidence[(item['symbol'], item['side'])]
        result[key] = selected[:limit]
    result['discount_diagnostics'] = [{'symbol': i['symbol'], 'side': i['side'], **i['futures_discount']}
        for i in rows if i['futures_discount']['status'] != 'MATCH']
    result['reviewed'] = [i for i in rows if (i['symbol'], i['side']) in matches]
    result['approved_count'] = sum(i.get('final_decision') == 'APPROVED' for i in result['reviewed'])
    result['entry_quality_shortlist_limit'] = limit
    return result


def scan_imp_future(settings, database, limit, mode, run_id):
    # One shared scan, with full ranked lists retained until the discount filter runs.
    return scan_ai_stock(settings, database, limit, mode, run_id, all_universe=True,
        title='Imp Future', cache_name='imp_future_prepared', history_class=ImpFutureHistory,
        saved_id='_imp_future_saved_scan_id', postprocess=lambda report: filter_discount_report(report, limit))


def render(platform, database, start_job, is_running):
    import streamlit as st
    st.caption('Imp Future requires Futures to trade at least 1% below their underlying stock price. '
               'For a ₹100 stock, Futures must be ₹99 or lower. The shared scanner evaluates both LONG and SHORT. '
               'Fresh paired quotes are required; after-market stale quotes cannot establish a live discount.')
    render_theme(platform, database, start_job, is_running, stocks={}, title='Imp Future',
        feature='imp_future', history_class=ImpFutureHistory, scan_function=scan_imp_future,
        connection_label='Universe', all_universe=True)
