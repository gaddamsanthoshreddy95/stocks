"""Versioned SQLite preparation cache; atomic records and safe concurrent readers."""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pandas as pd

SCHEMA = 1


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()


def candle_fingerprint(frame):
    if frame is None or frame.empty:
        return fingerprint(None)
    return sha256(pd.util.hash_pandas_object(frame, index=True).values.tobytes()).hexdigest()


class PreparationCache:
    def __init__(self, directory):
        root = Path(directory).expanduser()
        if not root.is_absolute():
            root = Path(__file__).resolve().parents[2] / root
        self.path = root.resolve() / 'prepared.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, identity TEXT, schema_version INTEGER, saved_at TEXT, expires_at TEXT, fingerprint TEXT, payload TEXT, PRIMARY KEY(kind,identity))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, kind, identity, payload, *, now, ttl, input_fingerprint=None):
        now = pd.Timestamp(now)
        if now.tz is None:
            now = now.tz_localize('Asia/Kolkata')
        encoded = json.dumps(payload, default=str, allow_nan=False)
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?)',
                (kind, identity, SCHEMA, now.isoformat(), (now+pd.Timedelta(seconds=ttl)).isoformat(), input_fingerprint, encoded))

    def get(self, kind, identity, *, now, expected_fingerprint=None, allow_expired=False):
        with self.connect() as db:
            row = db.execute('SELECT schema_version,saved_at,expires_at,fingerprint,payload FROM records WHERE kind=? AND identity=?', (kind,identity)).fetchone()
        if not row or row[0] != SCHEMA or (expected_fingerprint is not None and row[3] != expected_fingerprint):
            return None
        now = pd.Timestamp(now)
        now = now.tz_localize('Asia/Kolkata') if now.tz is None else now
        if not allow_expired and (now < pd.Timestamp(row[1]) or now >= pd.Timestamp(row[2])):
            return None
        try:
            return {'saved_at': row[1], 'expires_at': row[2], 'fingerprint': row[3], 'payload': json.loads(row[4])}
        except (ValueError, TypeError):
            return None

    def invalidate(self, kind, identity):
        with self.connect() as db:
            db.execute('DELETE FROM records WHERE kind=? AND identity=?', (kind,identity))

    def has_frame(self, identity):
        """Check retained history without rebuilding hundreds of pandas frames."""
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM records WHERE kind='candles' AND identity=? AND schema_version=? "
                             "AND json_valid(payload) AND json_array_length(payload,'$.index')>0", (identity,SCHEMA)).fetchone()
        return row is not None

    def put_frame(self, identity, frame, *, now, ttl):
        frame = frame.copy().sort_index()
        index = pd.DatetimeIndex(frame.index)
        frame.index = index.tz_localize('Asia/Kolkata') if index.tz is None else index.tz_convert('Asia/Kolkata')
        self.put('candles', identity, {'index': [str(value) for value in frame.index],
            'columns': list(frame.columns), 'values': frame.astype(object).where(frame.notna(), None).values.tolist()},
            now=now, ttl=ttl, input_fingerprint=candle_fingerprint(frame))

    def frame(self, identity, *, now, allow_expired=False):
        record = self.get('candles', identity, now=now, allow_expired=allow_expired)
        if record is None:
            return None
        payload = record['payload']
        return pd.DataFrame(payload['values'], columns=payload['columns'], index=pd.DatetimeIndex(pd.to_datetime(payload['index'], utc=True)).tz_convert('Asia/Kolkata'))
