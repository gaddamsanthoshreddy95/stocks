"""Durable reconciliation of manual Futures executions. Contains no broker mutations."""
from collections import defaultdict
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sqlite3
from src.futures.sessions import ist


def unknown(reason, now=None):
    return {'status': 'UNKNOWN', 'executed_entries': None, 'remaining_entries': None,
            'reasons': [reason], 'checked_at': ist(now).isoformat() if now is not None else None,
            'entries': [], 'pending_orders': [], 'overnight_positions': [], 'read_only': True}


def integer(value):
    number = float(value)
    if not isfinite(number) or number != int(number):
        raise ValueError('NON_INTEGER_BROKER_QUANTITY')
    return int(number)


class FuturesTradeLedger:
    def __init__(self, path, maximum_entries=2, max_snapshot_age_seconds=120):
        if maximum_entries != 2:
            raise ValueError('Global executed entry limit must be two')
        if not isfinite(float(max_snapshot_age_seconds)) or max_snapshot_age_seconds <= 0:
            raise ValueError('Snapshot age must be positive and finite')
        path = Path(path).expanduser()
        self.path = path if path.is_absolute() else Path(__file__).resolve().parents[2]/path
        self.maximum_entries = maximum_entries
        self.max_age = max_snapshot_age_seconds

    def reconcile(self, snapshot, now, instruments):
        now = ist(now)
        try:
            if not isinstance(snapshot, dict) or snapshot.get('status') != 'PASS':
                reasons = snapshot.get('reasons', []) if isinstance(snapshot, dict) else ['INVALID_SNAPSHOT']
                return unknown('BROKER_RECONCILIATION_UNAVAILABLE: '+str(reasons), now)
            checked = ist(snapshot['checked_at'])
            if checked.date() != now.date() or not 0 <= (now-checked).total_seconds() <= self.max_age:
                raise ValueError('BROKER_RECONCILIATION_STALE')
            account = snapshot['account_id']
            if not isinstance(account, str) or not account.strip():
                raise ValueError('BROKER_ACCOUNT_UNVERIFIED')
            account = sha256(('KITE:'+account).encode()).hexdigest()
            result, fills = self._classify(snapshot, now, instruments)
            day = now.date().isoformat()
            result.update(account_fingerprint=account, trading_day=day, checked_at=checked.isoformat(), read_only=True,
                limit=2, scope='All NSE Futures executions in the linked account, LONG and SHORT; candidates consume no slots',
                limitation='Snapshot eligibility only; cannot prevent manual/external orders after reconciliation')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path, timeout=10) as db:
                db.execute('PRAGMA busy_timeout=10000')
                db.execute('CREATE TABLE IF NOT EXISTS fills(account TEXT, day TEXT, trade_id TEXT, payload TEXT, PRIMARY KEY(account,day,trade_id))')
                db.execute('CREATE TABLE IF NOT EXISTS snapshots(account TEXT, day TEXT, checked_at TEXT, payload TEXT, PRIMARY KEY(account,day))')
                db.execute('CREATE TABLE IF NOT EXISTS ledger_identity(singleton INTEGER PRIMARY KEY CHECK(singleton=1), account TEXT NOT NULL)')
                db.execute('BEGIN IMMEDIATE')
                owner = db.execute('SELECT account FROM ledger_identity WHERE singleton=1').fetchone()
                if owner and owner[0] != account:
                    raise ValueError('BROKER_ACCOUNT_CHANGED_RECONCILIATION_REQUIRED')
                historical_accounts = {row[0] for row in db.execute('SELECT DISTINCT account FROM snapshots')}
                if historical_accounts - {account}:
                    raise ValueError('BROKER_ACCOUNT_CHANGED_RECONCILIATION_REQUIRED')
                db.execute('INSERT OR IGNORE INTO ledger_identity VALUES (1,?)', (account,))
                prior = dict(db.execute('SELECT trade_id,payload FROM fills WHERE account=? AND day=?', (account, day)))
                encoded = {key: json.dumps(value, sort_keys=True) for key, value in fills.items()}
                if any(key not in encoded or encoded[key] != value for key, value in prior.items()):
                    raise ValueError('BROKER_FILL_HISTORY_MISSING_OR_REVISED_RECONCILIATION_REQUIRED')
                old = db.execute('SELECT checked_at FROM snapshots WHERE account=? AND day=?', (account, day)).fetchone()
                if old and ist(old[0]) > checked:
                    raise ValueError('OLDER_SNAPSHOT_CANNOT_REPLACE_NEWER_RECONCILIATION')
                for key, value in encoded.items():
                    db.execute('INSERT OR IGNORE INTO fills VALUES (?,?,?,?)', (account, day, key, value))
                db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)', (account, day, checked.isoformat(), json.dumps(result)))
            return result
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, OSError, sqlite3.Error) as exc:
            return unknown(str(exc), now)

    def _classify(self, snapshot, now, instruments):
        if hasattr(instruments, 'to_dict'):
            instruments = instruments.to_dict('records')
        master = {}
        for item in instruments:
            if not isinstance(item, dict):
                raise ValueError('INSTRUMENT_MASTER_INVALID')
            symbol = item['tradingsymbol']
            if item.get('exchange', 'NFO') != 'NFO':
                continue
            if symbol in master and master[symbol] != item:
                raise ValueError('INSTRUMENT_MASTER_AMBIGUOUS:'+symbol)
            master[symbol] = item
        def future(row):
            if not isinstance(row, dict) or not row.get('exchange'):
                raise ValueError('BROKER_ROW_EXCHANGE_MISSING')
            if row['exchange'] != 'NFO':
                return False
            symbol = row['tradingsymbol']
            if symbol not in master:
                raise ValueError('BROKER_INSTRUMENT_UNVERIFIED:'+symbol)
            if (row.get('instrument_token') is not None and master[symbol].get('instrument_token') is not None
                    and str(row['instrument_token']) != str(master[symbol]['instrument_token'])):
                raise ValueError('BROKER_INSTRUMENT_TOKEN_MISMATCH:'+symbol)
            if master[symbol].get('instrument_type') not in ('FUT', 'CE', 'PE'):
                raise ValueError('BROKER_INSTRUMENT_TYPE_UNVERIFIED:'+symbol)
            return master[symbol].get('instrument_type') == 'FUT'
        def key(row):
            return row['tradingsymbol'], row['product']
        orders = {}
        if not isinstance(snapshot['orders'], list) or not isinstance(snapshot['trades'], list):
            raise ValueError('BROKER_ORDER_OR_TRADE_BOOK_INCOMPLETE')
        for row in snapshot['orders']:
            if not future(row):
                continue
            oid = str(row['order_id'])
            if not row['order_id'] or oid in orders:
                raise ValueError('DUPLICATE_ORDER_RECORD')
            quantity, filled = integer(row['quantity']), integer(row['filled_quantity'])
            if quantity <= 0 or not 0 <= filled <= quantity or row['transaction_type'] not in ('BUY', 'SELL') or not row.get('product') or not row.get('status'):
                raise ValueError('INVALID_BROKER_ORDER')
            if row['status'] == 'COMPLETE' and filled != quantity:
                raise ValueError('COMPLETE_ORDER_QUANTITY_MISMATCH')
            orders[oid] = row
        positions = {}
        net = snapshot['positions']['net']
        if not isinstance(net, list) or not isinstance(snapshot['positions'].get('day'), list):
            raise ValueError('BROKER_POSITION_BOOK_INCOMPLETE')
        for row in net:
            if future(row):
                if key(row) in positions:
                    raise ValueError('DUPLICATE_POSITION_RECORD')
                positions[key(row)] = row
        day_positions = {}
        for row in snapshot['positions']['day']:
            if future(row):
                if key(row) in day_positions:
                    raise ValueError('DUPLICATE_DAY_POSITION_RECORD')
                day_positions[key(row)] = row
        fills, grouped = {}, defaultdict(list)
        for row in snapshot['trades']:
            if not future(row):
                continue
            oid = str(row['order_id'])
            order = orders.get(oid)
            if not order:
                raise ValueError('FILL_WITHOUT_ORDER')
            qty = integer(row['quantity']);price = float(row['average_price'])
            stamp = ist(row.get('fill_timestamp') or row.get('exchange_timestamp'))
            side = row['transaction_type']
            if qty <= 0 or not isfinite(price) or price <= 0 or side not in ('BUY', 'SELL'):
                raise ValueError('INVALID_BROKER_FILL')
            if stamp.date() != now.date() or stamp > now:
                raise ValueError('FILL_TRADING_DAY_OR_TIMESTAMP_UNVERIFIED')
            if (row['tradingsymbol'] != order['tradingsymbol'] or side != order['transaction_type']
                    or row['product'] != order['product']):
                raise ValueError('ORDER_FILL_IDENTITY_MISMATCH')
            tid = str(row['trade_id'])
            if not row['trade_id']:
                raise ValueError('BROKER_TRADE_ID_MISSING')
            value = {'trade_id': tid, 'order_id': oid, 'tradingsymbol': row['tradingsymbol'], 'product': order['product'],
                     'side': side, 'quantity': qty, 'price': price, 'timestamp': stamp.isoformat()}
            if tid in fills:
                if fills[tid] != value:
                    raise ValueError('CONFLICTING_DUPLICATE_FILL')
                continue
            fills[tid] = value
            grouped[key(order)].append(value)
        totals = defaultdict(int)
        for fill in fills.values():
            totals[fill['order_id']] += fill['quantity']
        for oid, order in orders.items():
            if integer(order['filled_quantity']) != totals[oid]:
                raise ValueError('ORDER_FILLED_QUANTITY_DOES_NOT_MATCH_TRADES')
        entries, carry, pending = {}, [], []
        for position_key in set(positions)|set(grouped)|set(day_positions):
            pos = positions.get(position_key)
            if pos is None:
                raise ValueError('FILLS_WITHOUT_RECONCILED_POSITION')
            rows = sorted(grouped[position_key], key=lambda x: (x['timestamp'], x['trade_id']))
            opening = integer(pos['overnight_quantity'])
            buy = sum(x['quantity'] for x in rows if x['side'] == 'BUY')
            sell = sum(x['quantity'] for x in rows if x['side'] == 'SELL')
            # Kite net buy/sell_quantity includes carried inventory; the explicit
            # day quantities are the only appropriate comparison with today's fills.
            if buy != integer(pos['day_buy_quantity']) or sell != integer(pos['day_sell_quantity']) or opening+buy-sell != integer(pos['quantity']):
                raise ValueError('POSITION_FILL_RECONCILIATION_MISMATCH_OR_CONVERSION')
            day_pos = day_positions.get(position_key)
            if (buy or sell) and day_pos is None:
                raise ValueError('DAY_POSITION_MISSING_FOR_EXECUTED_FILLS')
            if day_pos is not None and (integer(day_pos['buy_quantity']) != buy or integer(day_pos['sell_quantity']) != sell
                    or integer(day_pos['quantity']) != buy-sell or integer(day_pos['overnight_quantity']) != 0):
                raise ValueError('DAY_POSITION_FILL_RECONCILIATION_MISMATCH')
            if opening:
                carry.append({'tradingsymbol': position_key[0], 'product': position_key[1], 'overnight_quantity': opening})
            last_stamp, last_side = None, None
            quantity = opening
            for fill in rows:
                if last_stamp == fill['timestamp'] and last_side != fill['side']:
                    raise ValueError('AMBIGUOUS_FILL_ORDER_WITHIN_SAME_TIMESTAMP')
                last_stamp, last_side = fill['timestamp'], fill['side']
                signed = fill['quantity'] * (1 if fill['side'] == 'BUY' else -1)
                opened = abs(signed) if quantity == 0 or quantity*signed > 0 else max(0, abs(signed)-abs(quantity))
                quantity += signed
                if opened:
                    e = entries.setdefault(fill['order_id'], {'order_id': fill['order_id'], 'tradingsymbol': fill['tradingsymbol'],
                        'side': 'LONG' if signed > 0 else 'SHORT', 'quantity_opened': 0, 'entry_value': 0., 'first_fill_at': fill['timestamp']})
                    e['quantity_opened'] += opened;e['entry_value'] += opened*fill['price']
        for e in entries.values():
            e['actual_futures_average_entry'] = e.pop('entry_value')/e['quantity_opened']
            sign = 1 if e['side'] == 'LONG' else -1
            e.update(target=e['actual_futures_average_entry']*(1+sign*.003), stop_loss=e['actual_futures_average_entry']*(1-sign*.002),
                     price_basis='BROKER_CONFIRMED_OPENING_FILLS', submitted_orders=False)
        outstanding = defaultdict(list)
        for oid, order in orders.items():
            if order.get('status') not in ('COMPLETE', 'CANCELLED', 'REJECTED'):
                remaining = integer(order['quantity'])-integer(order['filled_quantity'])
                if remaining < 0:
                    raise ValueError('INVALID_PENDING_QUANTITY')
                quantity = integer(positions.get(key(order), {}).get('quantity', 0))
                signed = 1 if order['transaction_type'] == 'BUY' else -1
                if remaining:
                    outstanding[(key(order), signed)].append((oid, remaining))
                if remaining and (quantity*signed >= 0 or remaining > abs(quantity)):
                    pending.append(oid)
        # Two apparently protective exits can jointly reverse a position. Their
        # combined unfilled quantity must not be mistaken for harmless exits.
        for (position_key, sign), rows in outstanding.items():
            quantity = integer(positions.get(position_key, {}).get('quantity', 0))
            if sum(qty for _, qty in rows) > abs(quantity) or quantity*sign >= 0:
                pending.extend(oid for oid, _ in rows if oid not in pending)
        count = len(entries)
        reasons = []
        if count >= 2:reasons.append('GLOBAL_TWO_EXECUTED_ENTRY_LIMIT_REACHED')
        if carry:reasons.append('OVERNIGHT_FUTURES_EXPOSURE_REQUIRES_MANUAL_REVIEW')
        status = 'FAIL' if reasons else 'UNKNOWN' if pending else 'PASS'
        if pending:reasons.append('PENDING_ENTRY_ORDERS_CAPACITY_UNCERTAIN')
        return {'status': status, 'executed_entries': count, 'remaining_entries': max(0, 2-count),
                'reasons': reasons or ['EXECUTED_ENTRIES_RECONCILED'],
                'entries': sorted(entries.values(), key=lambda e: (e['first_fill_at'], e['order_id'])),
                'pending_orders': sorted(pending),
                'overnight_positions': sorted(carry, key=lambda p: (p['tradingsymbol'], p['product']))}, fills
