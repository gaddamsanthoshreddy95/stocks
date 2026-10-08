from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from requests.exceptions import Timeout

from src.futures.gateway import KiteGateway
from src.futures.runtime import ScanRuntime
from test_futures_trade_ledger import NOW, fill, snapshot


def gateway(book=None, **runtime):
    book = book or snapshot()
    client = Mock()
    client.api_key = 'read-only-test-key'
    client.profile.return_value = {'user_id': book['account_id'], 'email': 'not-retained@example.test'}
    client.orders.return_value = book['orders']
    client.trades.return_value = book['trades']
    client.positions.return_value = book['positions']
    for name in ('place_order', 'modify_order', 'cancel_order', 'convert_position', 'place_gtt', 'modify_gtt', 'delete_gtt'):
        getattr(client, name).side_effect = AssertionError('Forbidden broker mutation: '+name)
    return KiteGateway(SimpleNamespace(provider=SimpleNamespace(kite=client)), ScanRuntime(**runtime)), client


def test_snapshot_reads_only_and_does_not_retain_profile_pii():
    transport, client = gateway(snapshot([fill('A')]))
    with patch('src.futures.gateway.sleep'):
        result = transport.account_snapshot(now=NOW)
    assert result['status'] == 'PASS' and result['read_only']
    assert result['checked_at'] == NOW.isoformat()
    assert 'email' not in result and 'profile' not in result
    assert [call[0] for call in client.method_calls] == ['profile', 'orders', 'trades', 'positions']*2
    assert transport.counts['account'] == 8


@pytest.mark.parametrize('api,field,value', [
    ('profile', 'user_id', 'CHANGED_ACCOUNT'),
    ('orders', 'filled_quantity', 9),
    ('trades', 'quantity', 9),
    ('trades', 'product', 'NRML'),
    ('trades', 'instrument_token', 999),
    ('orders', 'instrument_token', 999),
    ('positions', 'quantity', 9),
    ('positions', 'instrument_token', 999),
    ('positions', 'day_buy_quantity', 9),
])
def test_changed_books_fail_unknown(api, field, value):
    transport, client = gateway(snapshot([fill('A')]))
    first = deepcopy(getattr(client, api).return_value)
    second = deepcopy(first)
    target = second if api == 'profile' else second['net'][0] if api == 'positions' else second[0]
    target[field] = value
    getattr(client, api).side_effect = [first, second]
    with patch('src.futures.gateway.sleep'):
        result = transport.account_snapshot(now=NOW)
    assert result['status'] == 'UNKNOWN'
    assert result['reasons'] == ['BROKER_BOOK_CHANGED_DURING_RECONCILIATION']


def test_price_only_position_changes_do_not_invalidate_execution_snapshot():
    transport, client = gateway(snapshot([fill('A')]))
    first = deepcopy(client.positions.return_value);second = deepcopy(first)
    first['net'][0].update(last_price=100, pnl=0)
    second['net'][0].update(last_price=101, pnl=10)
    client.positions.side_effect = [first, second]
    with patch('src.futures.gateway.sleep'):
        result = transport.account_snapshot(now=NOW)
    assert result['status'] == 'PASS'


def test_broker_timeout_is_bounded_and_unknown_without_exposing_failure_payload():
    transport, client = gateway(retries=1)
    client.orders.side_effect = Timeout('sensitive response should not be shown')
    with patch('src.futures.gateway.sleep'):
        result = transport.account_snapshot(now=NOW)
    assert result['status'] == 'UNKNOWN' and client.orders.call_count == 2
    assert result['reasons'] == ['BROKER_ACCOUNT_READ_FAILED:Timeout']
    assert not client.trades.called and not client.place_order.called


def test_snapshot_deadline_failure_calls_no_broker_method():
    transport, client = gateway()
    transport.deadline = 0
    result = transport.account_snapshot(now=NOW)
    assert result['status'] == 'UNKNOWN'
    assert result['reasons'] == ['BROKER_ACCOUNT_READ_FAILED:TimeoutError']
    assert not client.method_calls
