"""Canonical NSE candle clock. Raw feeds are retained; no missing bars are invented."""
import os
import pandas as pd

CALCULATION_VERSION = 'FUTURES_ENTRY_SESSION_V2'


def ist(value):
    value = pd.Timestamp(value)
    if pd.isna(value):
        raise ValueError('Invalid exchange timestamp')
    return value.tz_localize('Asia/Kolkata') if value.tz is None else value.tz_convert('Asia/Kolkata')


def trading_day(day):
    holidays = {v.strip() for v in os.getenv('MARKET_HOLIDAYS_IST', '').split(',')}
    return day.weekday() < 5 and day.date().isoformat() not in holidays


def infer_interval(frame):
    index = pd.DatetimeIndex(frame.index)
    return 'day' if all((x.hour, x.minute, x.second) == (0, 0, 0) for x in index) else '5minute'


def normalise_candles(frame, interval=None, now=None, allow_live_daily=False):
    if frame is None or frame.empty:
        raise ValueError('CANDLES_MISSING')
    frame = frame.copy()
    idx = pd.DatetimeIndex(frame.index)
    frame.index = idx.tz_localize('Asia/Kolkata') if idx.tz is None else idx.tz_convert('Asia/Kolkata')
    interval = interval or infer_interval(frame)
    if interval not in ('day', '5minute'):
        raise ValueError('Explicit day or 5minute interval required')
    if frame.index.hasnans or frame.index.has_duplicates:
        raise ValueError('CANDLE_TIMESTAMPS_INVALID_OR_DUPLICATED')
    initial = len(frame)
    mask = [trading_day(x) for x in frame.index]
    if interval == '5minute':
        mask = [ok and (9, 15) <= (x.hour, x.minute) < (15, 30)
                and x.minute % 5 == 0 and x.second == 0 and x.microsecond == 0 and x.nanosecond == 0
                for ok, x in zip(mask, frame.index)]
    else:
        mask = [ok and (x.hour, x.minute, x.second, x.microsecond, x.nanosecond) == (0, 0, 0, 0, 0)
                for ok, x in zip(mask, frame.index)]
    excluded = [x.isoformat() for x, ok in zip(frame.index, mask) if not ok]
    frame = frame.loc[mask].sort_index()
    incomplete = 0
    if now is not None:
        now = ist(now)
        if interval == '5minute':
            done = frame.index + pd.Timedelta(minutes=5) <= now
        else:
            done = frame.index + pd.Timedelta(hours=15, minutes=30) <= now
            if allow_live_daily:
                live = frame.get('IS_LIVE_CANDLE', pd.Series(False, index=frame.index)).fillna(False).astype(bool)
                done = done | ((frame.index.date == now.date()) & live & (frame.index <= now))
        incomplete = int((~done).sum())
        frame = frame.loc[done]
    frame.attrs['session_normalization'] = {'version': CALCULATION_VERSION, 'interval': interval,
        'input_rows': initial, 'excluded_rows': len(excluded), 'excluded_timestamps': excluded,
        'incomplete_rows_excluded': incomplete, 'output_rows': len(frame)}
    return frame


def expected_completed_bar(now):
    now = ist(now)
    if not trading_day(now) or (now.hour, now.minute) < (9, 20):
        return None
    return min(now.floor('5min')-pd.Timedelta(minutes=5), now.normalize()+pd.Timedelta(hours=15, minutes=25))


def completed_session_check(frame, now):
    expected = expected_completed_bar(now)
    result = {'status': 'UNKNOWN', 'reason_codes': [], 'expected_latest': expected.isoformat() if expected is not None else None,
              'candle_timestamp': None, 'missing_candles': []}
    try:
        data = normalise_candles(frame, '5minute', now)
        if expected is None or data.empty:
            raise ValueError('NO_COMPLETED_CURRENT_SESSION')
        day = data.loc[data.index.date == expected.date()]
        grid = pd.date_range(expected.normalize()+pd.Timedelta(hours=9, minutes=15), expected, freq='5min')
        result['missing_candles'] = [v.isoformat() for v in grid.difference(day.index)]
        result['candle_timestamp'] = day.index[-1].isoformat() if not day.empty else None
        if day.empty or day.index[-1] != expected or result['missing_candles']:
            raise ValueError('LATEST_COMPLETED_FUTURES_CANDLE_MISSING_OR_SESSION_INCOMPLETE')
        result.update(status='PASS', reason_codes=['LATEST_COMPLETED_SESSION_VERIFIED'])
    except (ValueError, TypeError, KeyError) as exc:
        result['reason_codes'] = [str(exc)]
    return result


def entry_window(now, config):
    now = ist(now)
    cutoff = now.normalize()+pd.Timedelta(hours=config.entry_cutoff_hour, minutes=config.entry_cutoff_minute)
    exit_at = now.normalize()+pd.Timedelta(hours=config.intraday_exit_hour, minutes=config.intraday_exit_minute)
    open_ = trading_day(now) and (9, 20) <= (now.hour, now.minute) and now < cutoff
    return {'status': 'PASS' if open_ else 'FAIL', 'reason_codes': ['INTRADAY_ENTRY_WINDOW_OPEN' if open_ else 'INTRADAY_ENTRY_WINDOW_CLOSED'],
        'entry_cutoff': cutoff.isoformat(), 'manual_exit_deadline': exit_at.isoformat(),
        'manual_exit_warning_active': trading_day(now) and now >= exit_at-pd.Timedelta(minutes=config.manual_exit_warning_minutes),
        'manual_exit_warning': 'Manually close intraday Futures in Zerodha Kite before '+exit_at.strftime('%H:%M')+' IST. This scanner never places, modifies, cancels, converts or squares off orders.',
        'automatic_square_off': False}
