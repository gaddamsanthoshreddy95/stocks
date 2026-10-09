"""Read-only Kite transport with shared pacing, bounded retries and deadlines."""
from threading import Lock
from hashlib import sha256
from time import monotonic, sleep

from requests.exceptions import ConnectionError, Timeout
from kiteconnect.exceptions import NetworkException, DataException


class KiteGateway:
    _scope_lock = Lock()
    _scopes = {}
    def __init__(self, provider, runtime, deadline=None):
        self.provider, self.runtime, self.deadline = provider, runtime, deadline
        self.kite = provider.provider.kite
        # SDK public timeout is read by every HTTP request.
        self.kite.timeout = runtime.request_timeout_seconds
        key = getattr(self.kite,'api_key',None)
        scope = sha256(key.encode()).hexdigest() if isinstance(key,str) else id(self.kite)
        with self._scope_lock:
            self.locks,self.last = self._scopes.setdefault(scope,(
                {name:Lock() for name in ('quote','history','margin','account')},
                {name:0. for name in ('quote','history','margin','account')}))
        self.counts = {name: 0 for name in self.locks}

    def request(self, kind, operation):
        interval = {'quote': self.runtime.quote_interval_seconds,
                    'history': self.runtime.historical_interval_seconds, 'margin': .11, 'account': .11}[kind]
        with self.locks[kind]:
            for attempt in range(self.runtime.retries+1):
                delay = max(0, interval-(monotonic()-self.last[kind]))
                if self.deadline is not None and monotonic()+delay+self.runtime.request_timeout_seconds > self.deadline:
                    raise TimeoutError('LIVE_SCAN request budget exhausted')
                if delay:
                    sleep(delay)
                self.last[kind] = monotonic()
                self.counts[kind] += 1
                try:
                    return operation()
                except (ConnectionError, Timeout, NetworkException, DataException):
                    if attempt == self.runtime.retries:
                        raise
            raise RuntimeError('Unreachable retry state')

    def quotes(self, keys):
        result = {}
        for offset in range(0, len(keys), 500):
            response = self.request('quote', lambda: self.kite.quote(keys[offset:offset+500]))
            if not isinstance(response, dict):
                raise ValueError('Invalid quote response')
            result.update(response)
        return result

    def history(self, token, start, end, interval, *, oi=False):
        from src.providers.kite_provider import KiteProvider
        call = self.provider.provider.request_historical_data if isinstance(self.provider.provider, KiteProvider) else self.kite.historical_data
        return self.request('history', lambda: call(token, start, end, interval, continuous=False, oi=oi))

    def margins(self, orders):
        result = []
        for offset in range(0, len(orders), 50):
            result.extend(self.request('margin', lambda: self.kite.order_margins(orders[offset:offset+50])) or [])
        funds = self.request('margin', lambda: self.kite.margins('equity'))
        return result, funds

    def account_snapshot(self, now=None):
        """Only GET APIs. Compare two books to detect fills during reconciliation."""
        import json
        import pandas as pd
        from src.futures.sessions import ist
        def read():
            profile = self.request('account', self.kite.profile)
            orders = self.request('account', self.kite.orders)
            trades = self.request('account', self.kite.trades)
            positions = self.request('account', self.kite.positions)
            if not isinstance(profile, dict) or not isinstance(orders, list) or not isinstance(trades, list) or not isinstance(positions, dict):
                raise ValueError('Invalid account response')
            return {'account_id': profile['user_id'], 'orders': orders, 'trades': trades, 'positions': positions}
        def signature(book):
            # Exclude mark-to-market/P&L changes; retain execution quantities and identity.
            position_keys = ('exchange','tradingsymbol','instrument_token','product','quantity','overnight_quantity','buy_quantity','sell_quantity','day_buy_quantity','day_sell_quantity')
            order_keys = ('order_id','exchange','tradingsymbol','instrument_token','product','status','quantity','filled_quantity','transaction_type')
            trade_keys = ('trade_id','order_id','exchange','tradingsymbol','instrument_token','product','transaction_type','quantity','average_price','fill_timestamp','exchange_timestamp')
            project=lambda rows, keys: sorted([{k:r.get(k) for k in keys} for r in rows], key=lambda r:json.dumps(r,sort_keys=True,default=str))
            return json.dumps({'account_id': book['account_id'], 'orders':project(book['orders'],order_keys),
                'trades':project(book['trades'],trade_keys), 'positions':{k:project(book['positions'][k],position_keys) for k in ('net','day')}},sort_keys=True,default=str)
        try:
            before = read();after = read()
            if signature(before) != signature(after):
                return {'status':'UNKNOWN','reasons':['BROKER_BOOK_CHANGED_DURING_RECONCILIATION']}
            return {**after,'status':'PASS','checked_at':ist(now if now is not None else pd.Timestamp.now(tz='Asia/Kolkata')).isoformat(),'read_only':True}
        except Exception as exc:
            return {'status':'UNKNOWN','reasons':['BROKER_ACCOUNT_READ_FAILED:'+type(exc).__name__], 'read_only':True}
