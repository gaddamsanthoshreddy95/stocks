from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3

import pandas as pd
import pytest

from src.futures.trade_ledger import FuturesTradeLedger


NOW = pd.Timestamp('2026-10-08 11:00:00', tz='Asia/Kolkata')
MASTER = [
    {'tradingsymbol': 'AAA26OCTFUT', 'instrument_type': 'FUT', 'exchange': 'NFO', 'instrument_token': 101},
    {'tradingsymbol': 'BBB26NOVFUT', 'instrument_type': 'FUT', 'exchange': 'NFO', 'instrument_token': 102},
    {'tradingsymbol': 'AAA26OCT100CE', 'instrument_type': 'CE', 'exchange': 'NFO', 'instrument_token': 103},
]


def fill(order, side='BUY', quantity=10, minute=20, symbol='AAA26OCTFUT', trade=None, price=100., product='MIS'):
    return {'order_id': str(order), 'trade_id': str(trade or order), 'exchange': 'NFO', 'tradingsymbol': symbol,
            'product': product, 'transaction_type': side, 'quantity': quantity, 'average_price': price,
            'fill_timestamp': NOW.normalize()+pd.Timedelta(hours=9, minutes=minute)}


def snapshot(fills=(), *, opening=None, now=NOW, pending=()):
    opening = opening or {}
    orders = {}
    net = {}
    day = {}
    for f in fills:
        oid = f['order_id']
        order = orders.setdefault(oid, {k: f[k] for k in ('order_id', 'exchange', 'tradingsymbol', 'product', 'transaction_type')})
        order.update(status='COMPLETE', quantity=order.get('quantity', 0)+f['quantity'],
                     filled_quantity=order.get('filled_quantity', 0)+f['quantity'])
    keys = set(opening)|{(f['tradingsymbol'], f['product']) for f in fills}
    for symbol, product in keys:
        rows = [f for f in fills if (f['tradingsymbol'], f['product']) == (symbol, product)]
        buy = sum(f['quantity'] for f in rows if f['transaction_type'] == 'BUY')
        sell = sum(f['quantity'] for f in rows if f['transaction_type'] == 'SELL')
        carry = opening.get((symbol, product), 0)
        common = {'exchange': 'NFO', 'tradingsymbol': symbol, 'product': product}
        net[(symbol, product)] = {**common, 'quantity': carry+buy-sell, 'overnight_quantity': carry,
            'buy_quantity': buy+max(carry, 0), 'sell_quantity': sell+max(-carry, 0),
            'day_buy_quantity': buy, 'day_sell_quantity': sell}
        if rows:
            day[(symbol, product)] = {**common, 'quantity': buy-sell, 'overnight_quantity': 0,
                'buy_quantity': buy, 'sell_quantity': sell, 'day_buy_quantity': buy, 'day_sell_quantity': sell}
    return {'status': 'PASS', 'account_id': 'TEST_ACCOUNT', 'checked_at': now.isoformat(), 'read_only': True,
        'orders': list(orders.values())+list(pending), 'trades': deepcopy(list(fills)),
        'positions': {'net': list(net.values()), 'day': list(day.values())}}


def pending_order(oid='P', side='BUY', quantity=10, symbol='AAA26OCTFUT'):
    return {'order_id': oid, 'transaction_type': side, 'quantity': quantity, 'filled_quantity': 0,
            'status': 'OPEN', 'product': 'MIS', 'tradingsymbol': symbol, 'exchange': 'NFO'}


@pytest.fixture
def ledger(tmp_path):
    return FuturesTradeLedger(tmp_path/'executed.sqlite3')


def test_empty_confirmed_book_does_not_count_candidates(ledger):
    result = ledger.reconcile(snapshot(), NOW, MASTER)
    assert result['status'] == 'PASS'
    assert result['executed_entries'] == 0 and result['remaining_entries'] == 2
    assert result['read_only'] and result['entries'] == []


def test_global_count_long_short_symbols_expiries_and_api_manual_orders(ledger):
    book = snapshot([fill('manual-long'), fill('api-short', side='SELL', symbol='BBB26NOVFUT')])
    book['orders'][0]['placed_by'] = 'TEST_ACCOUNT'
    book['orders'][1]['tag'] = 'external-api'
    result = ledger.reconcile(book, NOW, MASTER)
    assert result['executed_entries'] == 2 and result['remaining_entries'] == 0
    assert result['status'] == 'FAIL'
    assert {e['side'] for e in result['entries']} == {'LONG', 'SHORT'}


def test_partial_fills_duplicates_and_restart_count_order_once(ledger):
    first = fill('O', quantity=4, trade='T1', price=100)
    book = snapshot([first])
    book['orders'][0].update(status='OPEN', quantity=10)
    assert ledger.reconcile(book, NOW, MASTER)['executed_entries'] == 1
    second = fill('O', quantity=6, trade='T2', minute=21, price=102)
    completed = snapshot([first, second])
    completed['trades'].append(deepcopy(first))
    result = FuturesTradeLedger(ledger.path).reconcile(completed, NOW, MASTER)
    assert result['status'] == 'PASS' and result['executed_entries'] == 1
    entry = result['entries'][0]
    assert entry['actual_futures_average_entry'] == pytest.approx(101.2)
    assert entry['target'] == pytest.approx(101.2*1.003)
    assert entry['stop_loss'] == pytest.approx(101.2*.998)
    assert entry['quantity_opened'] == 10 and entry['price_basis'] == 'BROKER_CONFIRMED_OPENING_FILLS'
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT count(*) FROM fills').fetchone()[0] == 2


@pytest.mark.parametrize('fills,expected', [
    ([fill('E'), fill('X', side='SELL', minute=21)], 1),
    ([fill('E'), fill('X1', side='SELL', quantity=4, minute=21), fill('X2', side='SELL', quantity=6, minute=22)], 1),
    ([fill('E'), fill('X', side='SELL', minute=21), fill('R', minute=22)], 2),
    ([fill('E'), fill('INCREASE', minute=21)], 2),
    ([fill('E'), fill('REVERSAL', side='SELL', quantity=15, minute=21)], 2),
    ([fill('E', side='SELL'), fill('REVERSAL', quantity=15, minute=21)], 2),
])
def test_entries_exits_reentry_scale_in_and_reversal(ledger, fills, expected):
    result = ledger.reconcile(snapshot(fills), NOW, MASTER)
    assert result['executed_entries'] == expected
    assert result['status'] == ('FAIL' if expected == 2 else 'PASS')
    if 'REVERSAL' in fills[-1]['order_id']:
        reversal = next(e for e in result['entries'] if e['order_id'] == 'REVERSAL')
        assert reversal['quantity_opened'] == 5


def test_cancelled_or_rejected_unfilled_orders_consume_no_trade(ledger):
    cancelled = pending_order('C');cancelled['status'] = 'CANCELLED'
    rejected = pending_order('R');rejected['status'] = 'REJECTED'
    result = ledger.reconcile(snapshot(pending=[cancelled, rejected]), NOW, MASTER)
    assert result['status'] == 'PASS' and result['executed_entries'] == 0


def test_cancelled_partially_filled_order_still_consumes_entry(ledger):
    book = snapshot([fill('C', quantity=4)])
    book['orders'][0].update(status='CANCELLED', quantity=10)
    result = ledger.reconcile(book, NOW, MASTER)
    assert result['status'] == 'PASS' and result['executed_entries'] == 1


def test_reconciliation_failure_does_not_erase_durable_fills(ledger):
    complete = snapshot([fill('A'), fill('B', symbol='BBB26NOVFUT', side='SELL')])
    assert ledger.reconcile(complete, NOW, MASTER)['executed_entries'] == 2
    truncated = snapshot([fill('A')])
    result = FuturesTradeLedger(ledger.path).reconcile(truncated, NOW, MASTER)
    assert result['status'] == 'UNKNOWN' and result['remaining_entries'] is None
    assert 'BROKER_FILL_HISTORY_MISSING' in result['reasons'][0]
    assert FuturesTradeLedger(ledger.path).reconcile(complete, NOW, MASTER)['executed_entries'] == 2


def test_revised_prior_fill_unknown_and_database_unchanged(ledger):
    book = snapshot([fill('A')]);ledger.reconcile(book, NOW, MASTER)
    changed = deepcopy(book);changed['trades'][0]['average_price'] = 101
    assert ledger.reconcile(changed, NOW, MASTER)['status'] == 'UNKNOWN'
    assert ledger.reconcile(book, NOW, MASTER)['entries'][0]['actual_futures_average_entry'] == 100


def test_same_timestamp_opposite_fills_unknown_not_arbitrary_trade_id_order(ledger):
    result = ledger.reconcile(snapshot([fill('A'), fill('B', side='SELL')]), NOW, MASTER)
    assert result['status'] == 'UNKNOWN'
    assert 'AMBIGUOUS_FILL_ORDER' in result['reasons'][0]


def test_pending_entry_unknown_but_unfilled_order_not_counted(ledger):
    result = ledger.reconcile(snapshot(pending=[pending_order()]), NOW, MASTER)
    assert result['status'] == 'UNKNOWN' and result['executed_entries'] == 0
    assert result['pending_orders'] == ['P']


def test_exit_order_is_not_entry_but_combined_exits_can_reverse(ledger):
    close = pending_order('STOP', side='SELL')
    result = ledger.reconcile(snapshot([fill('A')], pending=[close]), NOW, MASTER)
    assert result['status'] == 'PASS'
    target = pending_order('TARGET', side='SELL')
    result = ledger.reconcile(snapshot([fill('A')], pending=[close, target]), NOW, MASTER)
    assert result['status'] == 'UNKNOWN'
    assert set(result['pending_orders']) == {'STOP', 'TARGET'}


def test_carried_inventory_exit_uses_day_quantities_not_net_buy_quantity(ledger):
    result = ledger.reconcile(snapshot([fill('EXIT', side='SELL')], opening={('AAA26OCTFUT', 'MIS'): 10}), NOW, MASTER)
    assert result['status'] == 'FAIL' and result['executed_entries'] == 0
    assert result['overnight_positions'][0]['overnight_quantity'] == 10
    assert result['reasons'] == ['OVERNIGHT_FUTURES_EXPOSURE_REQUIRES_MANUAL_REVIEW']


def test_overnight_position_without_today_fills_blocks_new_approval(ledger):
    result = ledger.reconcile(snapshot(opening={('AAA26OCTFUT', 'NRML'): -10}), NOW, MASTER)
    assert result['status'] == 'FAIL' and result['executed_entries'] == 0


@pytest.mark.parametrize('mutate,reason', [
    (lambda b: b['orders'][0].update(filled_quantity=9), 'COMPLETE_ORDER_QUANTITY_MISMATCH'),
    (lambda b: b['trades'].clear(), 'ORDER_FILLED_QUANTITY_DOES_NOT_MATCH_TRADES'),
    (lambda b: b['orders'].clear(), 'FILL_WITHOUT_ORDER'),
    (lambda b: b['positions']['net'][0].update(quantity=9), 'POSITION_FILL_RECONCILIATION'),
    (lambda b: b['positions']['day'].clear(), 'DAY_POSITION_MISSING'),
    (lambda b: b['positions']['day'][0].update(quantity=0), 'DAY_POSITION_FILL_RECONCILIATION'),
    (lambda b: b['trades'][0].update(product='NRML'), 'ORDER_FILL_IDENTITY'),
    (lambda b: b['trades'][0].update(average_price=float('nan')), 'INVALID_BROKER_FILL'),
    (lambda b: b['trades'][0].update(fill_timestamp=NOW+pd.Timedelta(seconds=1)), 'FILL_TRADING_DAY'),
    (lambda b: b['trades'][0].update(instrument_token=999), 'BROKER_INSTRUMENT_TOKEN_MISMATCH'),
    (lambda b: b['orders'][0].pop('exchange'), 'BROKER_ROW_EXCHANGE_MISSING'),
])
def test_incomplete_inconsistent_or_unverifiable_books_fail_unknown(ledger, mutate, reason):
    book = snapshot([fill('A')]);mutate(book)
    result = ledger.reconcile(book, NOW, MASTER)
    assert result['status'] == 'UNKNOWN' and result['remaining_entries'] is None
    assert reason in result['reasons'][0]


@pytest.mark.parametrize('seconds', [-1, 121, 86400])
def test_old_future_or_previous_day_snapshot_not_freshened(ledger, seconds):
    result = ledger.reconcile(snapshot(now=NOW-pd.Timedelta(seconds=seconds)), NOW, MASTER)
    assert result['status'] == 'UNKNOWN' and 'STALE' in result['reasons'][0]


def test_old_snapshot_cannot_replace_newer_even_with_same_fills(ledger):
    ledger.reconcile(snapshot([fill('A')]), NOW, MASTER)
    old = snapshot([fill('A')], now=NOW-pd.Timedelta(seconds=10))
    assert 'OLDER_SNAPSHOT' in ledger.reconcile(old, NOW, MASTER)['reasons'][0]


def test_account_switch_unknown_persistently_and_account_id_not_stored(ledger):
    book = snapshot([fill('A')]);ledger.reconcile(book, NOW, MASTER)
    other = snapshot();other['account_id'] = 'OTHER_ACCOUNT'
    result = FuturesTradeLedger(ledger.path).reconcile(other, NOW, MASTER)
    assert result['status'] == 'UNKNOWN' and 'ACCOUNT_CHANGED' in result['reasons'][0]
    assert b'TEST_ACCOUNT' not in ledger.path.read_bytes()
    assert ledger.reconcile(book, NOW, MASTER)['executed_entries'] == 1


def test_count_resets_by_ist_trading_day_not_restart(ledger):
    ledger.reconcile(snapshot([fill('A'), fill('B', symbol='BBB26NOVFUT')]), NOW, MASTER)
    tomorrow = NOW+pd.Timedelta(days=1)
    result = FuturesTradeLedger(ledger.path).reconcile(snapshot(now=tomorrow), tomorrow, MASTER)
    assert result['status'] == 'PASS' and result['remaining_entries'] == 2
    assert result['trading_day'] == '2026-10-09'


def test_concurrent_reconciliation_is_atomic_and_idempotent(ledger):
    book = snapshot([fill('A'), fill('B', side='SELL', symbol='BBB26NOVFUT')])
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: FuturesTradeLedger(ledger.path).reconcile(book, NOW, MASTER), range(12)))
    assert all(r['status'] == 'FAIL' and r['executed_entries'] == 2 for r in results)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute('SELECT count(*) FROM fills').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM snapshots').fetchone()[0] == 1


def test_database_error_and_invalid_snapshot_remain_unknown(tmp_path):
    path = tmp_path/'invalid.sqlite3';path.write_text('not a database')
    result = FuturesTradeLedger(path).reconcile(snapshot(), NOW, MASTER)
    assert result['status'] == 'UNKNOWN' and result['executed_entries'] is None
    assert FuturesTradeLedger(path).reconcile([], NOW, MASTER)['status'] == 'UNKNOWN'


def test_equity_and_options_do_not_count_but_unverified_nfo_is_unknown(ledger):
    book = snapshot([fill('OPT', symbol='AAA26OCT100CE')])
    book['orders'].append({'exchange': 'NSE', 'tradingsymbol': 'AAA'})
    assert ledger.reconcile(book, NOW, MASTER)['executed_entries'] == 0
    unknown = snapshot([fill('U', symbol='UNVERIFIED26OCTFUT')])
    assert 'INSTRUMENT_UNVERIFIED' in ledger.reconcile(unknown, NOW, MASTER)['reasons'][0]


def test_fixed_limit_cannot_be_increased(tmp_path):
    with pytest.raises(ValueError, match='two'):
        FuturesTradeLedger(tmp_path/'ledger.sqlite3', maximum_entries=3)
