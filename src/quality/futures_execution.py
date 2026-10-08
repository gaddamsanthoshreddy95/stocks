"""Explainable intraday futures gates. No margin lookup or order submission."""
from dataclasses import dataclass, replace
from datetime import datetime
from math import isfinite
import os
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from src.quality.models import QualityScore
from src.quality.futures_selection import session_vwap_quality
from src.futures.sessions import normalise_candles, completed_session_check

CHECKS = ('futures_atr_quality', 'futures_vwap_quality', 'futures_rvol_quality',
          'futures_oi_quality', 'futures_spread_quality', 'futures_depth_quality',
          'futures_adx_quality', 'futures_rsi_quality', 'futures_supertrend_quality',
          'futures_ema_quality', 'futures_sector_strength_quality',
          'futures_target_space_quality', 'futures_gap_quality', 'futures_event_quality')

@dataclass(frozen=True)
class FuturesExecutionConfig:
    minimum_daily_atr_percent: float = .3
    maximum_daily_atr_percent: float = 5
    minimum_five_minute_atr_percent: float = .03
    maximum_five_minute_atr_percent: float = 1
    minimum_rvol: float = 1.2
    maximum_spread_percent: float = .05
    minimum_depth_lots: float = 5
    minimum_adx: float = 25
    target_percent: float = .3
    maximum_gap_percent: float = 3
    minimum_volume_sessions: int = 5
    supertrend_multiplier: float = 3
    maximum_quote_age_seconds: float = 120
    maximum_candle_age_seconds: float = 600

    def __post_init__(self):
        for name, value in self.__dict__.items():
            if not isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if (self.minimum_daily_atr_percent > self.maximum_daily_atr_percent or
                self.minimum_five_minute_atr_percent > self.maximum_five_minute_atr_percent):
            raise ValueError('ATR minimum must not exceed maximum')
        if self.minimum_volume_sessions != int(self.minimum_volume_sessions):
            raise ValueError('minimum_volume_sessions must be an integer')

    @classmethod
    def from_env(cls):
        values = {name: float(os.getenv('FUTURES_'+name.upper(), str(value)))
                  for name, value in cls().__dict__.items()}
        return cls(**values)


def unknown(reason):
    return QualityScore(None, 'UNKNOWN', 0, reason_codes=[reason])


def check(passed, factors, reason):
    return QualityScore(100 if passed else 0, 'PASS' if passed else 'FAIL', 100,
                        factors, [reason])


def wilder(series, period=14):
    """Arithmetic seed followed by Wilder's recursive smoothing."""
    valid = series.dropna()
    values = np.full(len(series), float('nan'))
    out = pd.Series(values, index=series.index)
    if len(valid) < period:
        return out
    value = float(valid.iloc[:period].mean())
    positions = np.flatnonzero(series.notna().to_numpy())
    values[positions[period-1]] = value
    for position, number in zip(positions[period:], valid.to_numpy()[period:]):
        value = (value * (period - 1) + float(number)) / period
        values[position] = value
    return pd.Series(values, index=series.index)


def indicators(frame):
    previous = frame.Close.shift()
    tr = pd.concat([frame.High-frame.Low, (frame.High-previous).abs(),
                    (frame.Low-previous).abs()], axis=1).max(axis=1)
    atr = wilder(tr)
    up, down = frame.High.diff(), -frame.Low.diff()
    plus = wilder(up.where((up > down) & (up > 0), 0)) / atr * 100
    minus = wilder(down.where((down > up) & (down > 0), 0)) / atr * 100
    plus, minus = plus.mask(atr == 0, 0), minus.mask(atr == 0, 0)
    total = plus + minus
    dx = ((plus-minus).abs() / total * 100).mask(total == 0, 0)
    adx = wilder(dx)
    delta = frame.Close.diff()
    gain, loss = wilder(delta.clip(lower=0)), wilder(-delta.clip(upper=0))
    rsi = 100-100/(1+gain/loss)
    rsi = rsi.mask((loss == 0) & (gain > 0), 100).mask((loss == 0) & (gain == 0), 50)
    return atr, adx, rsi


def supertrend(frame, atr, multiplier):
    upper = (frame.High+frame.Low)/2 + multiplier*atr
    lower = (frame.High+frame.Low)/2 - multiplier*atr
    side = -1
    final_upper = final_lower = None
    previous_close = None
    for i in range(len(frame)):
        if pd.isna(atr.iloc[i]):
            continue
        u, l, close = float(upper.iloc[i]), float(lower.iloc[i]), float(frame.Close.iloc[i])
        if final_upper is None:
            final_upper, final_lower = u, l
        else:
            old_upper, old_lower = final_upper, final_lower
            final_upper = u if u < old_upper or previous_close > old_upper else old_upper
            final_lower = l if l > old_lower or previous_close < old_lower else old_lower
            if side == -1 and close > final_upper:
                side = 1
            elif side == 1 and close < final_lower:
                side = -1
        previous_close = close
    return side, final_lower if side == 1 else final_upper


def clean(frame):
    if frame is None or frame.empty or not {'Open','High','Low','Close','Volume'}.issubset(frame):
        raise ValueError('Missing OHLCV')
    frame = frame.copy().sort_index()
    idx = pd.DatetimeIndex(frame.index)
    frame.index = idx.tz_localize('Asia/Kolkata') if idx.tz is None else idx.tz_convert('Asia/Kolkata')
    if frame.index.has_duplicates:
        raise ValueError('Duplicate candles')
    cols = ['Open','High','Low','Close','Volume']
    frame[cols] = frame[cols].apply(pd.to_numeric, errors='coerce')
    if (not frame[cols].map(isfinite).all().all() or (frame.Volume < 0).any()
            or (frame[cols[:4]] <= 0).any().any() or (frame.High < frame.Low).any()
            or (frame.High < frame[['Open','Close']].max(axis=1)).any()
            or (frame.Low > frame[['Open','Close']].min(axis=1)).any()):
        raise ValueError('Invalid candles')
    return frame


def assess_execution(data, *, stock_history=None, sector_history=None, levels=None, event=None,
                     direction='BULLISH', config=None, now=None):
    config = config or FuturesExecutionConfig.from_env()
    now = pd.Timestamp(now or datetime.now(ZoneInfo('Asia/Kolkata')))
    now = now.tz_localize('Asia/Kolkata') if now.tz is None else now.tz_convert('Asia/Kolkata')
    result = {name: unknown('FUTURES_DATA_MISSING') for name in CHECKS}
    bullish = direction == 'BULLISH'
    if direction not in {'BULLISH','BEARISH'}:
        return result
    levels, event = levels or {}, event or {}
    try:
        stock, sector = clean(normalise_candles(stock_history, 'day')), clean(normalise_candles(sector_history, 'day'))
        aligned = pd.concat([stock.Close.rename('stock'), sector.Close.rename('sector')], axis=1).dropna()
        aligned = aligned.loc[aligned.index.date < now.date()]
        if len(aligned) >= 21 and (now.date()-aligned.index[-1].date()).days <= 7:
            sr = (float(aligned.stock.iloc[-1])/float(aligned.stock.iloc[-21])-1)*100
            br = (float(aligned.sector.iloc[-1])/float(aligned.sector.iloc[-21])-1)*100
            result['futures_sector_strength_quality'] = check(sr > br if bullish else sr < br,
                {'stock_20_session_return_percent':sr, 'sector_20_session_return_percent':br,
                 'excess_return_percentage_points':sr-br}, 'STOCK_VERSUS_SECTOR_ALIGNED_20_SESSIONS')
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        pass
    if event.get('event_data_availability_state') == 'COMPLETE':
        scheduled_risk = any(x.get('is_scheduled') and x.get('status') in {'ACTIVE', 'SCHEDULED', 'ESCALATING', 'UNKNOWN'}
                             and x.get('category') in {'EARNINGS', 'CORPORATE_ACTION'}
                             for x in event.get('matched_events', []))
        safe = (not event.get('hard_block', True) and not scheduled_risk
                and event.get('event_risk_level') in {'VERY_LOW','LOW'}
                and event.get('freshness_state') not in {'STALE','FAILED','DELAYED'})
        result['futures_event_quality'] = check(safe, {'event_risk_score':event.get('event_risk_score')}, 'COMPLETE_LOW_EVENT_RISK_REQUIRED')
    else:
        result['futures_event_quality'] = unknown('EVENT_COVERAGE_INCOMPLETE')
    if not data:
        return result
    quote = data.get('quote') or {}
    quote_checks = set(CHECKS) - {'futures_event_quality', 'futures_sector_strength_quality'}
    def unavailable(keys, reason):
        for key in keys:
            result[key] = unknown(reason)

    def finish():
        evidence = {'quote_timestamp': str(quote.get('timestamp', 'Not available')),
                    'checked_at': now.isoformat(),
                    'fetched_at': data.get('fetched_at', 'Not available'),
                    'contract': (data.get('contract') or {}).get('tradingsymbol', 'Not available')}
        for key in quote_checks:
            result[key] = replace(result[key], factors={**result[key].factors, **evidence})
        return result

    try:
        stamp = pd.Timestamp(quote['timestamp'])
        stamp = stamp.tz_localize('Asia/Kolkata') if stamp.tz is None else stamp.tz_convert('Asia/Kolkata')
        price = float(quote['last_price'])
        age = (now-stamp).total_seconds()
        if (pd.isna(stamp) or not isfinite(price) or price <= 0
                or not 0 <= age <= config.maximum_quote_age_seconds or stamp.date() != now.date()):
            raise ValueError('Stale quote')
        if (data.get('instrument_token') is not None and quote.get('instrument_token') is not None
                and int(data['instrument_token']) != int(quote['instrument_token'])):
            raise ValueError('Wrong contract quote')
    except (KeyError, TypeError, ValueError, OverflowError):
        unavailable(quote_checks, 'FUTURES_QUOTE_STALE_OR_INVALID')
        return finish()
    # Reports may run at any time; after-hours quotes cannot approve a live entry.
    holidays = {value.strip() for value in os.getenv('MARKET_HOLIDAYS_IST', '').split(',')}
    if (now.weekday() >= 5 or now.date().isoformat() in holidays
            or not (9, 15) <= (now.hour, now.minute) < (15, 30)):
        unavailable(quote_checks, 'FUTURES_MARKET_CLOSED')
        return finish()

    depth_keys = ['futures_spread_quality', 'futures_depth_quality', 'futures_target_space_quality']
    try:
        depth = quote['depth']
        def side(rows):
            valid = []
            for row in rows:
                value, quantity = float(row['price']), float(row['quantity'])
                if not isfinite(value) or not isfinite(quantity) or value < 0 or quantity < 0:
                    raise ValueError('Invalid depth')
                if value > 0 and quantity > 0:
                    valid.append((value, quantity))
            if not valid or len({value for value, _ in valid}) != len(valid):
                raise ValueError('Missing or duplicated depth levels')
            return valid
        bids, asks = side(depth['buy']), side(depth['sell'])
        bid, ask = max(value for value, _ in bids), min(value for value, _ in asks)
        if ask < bid:
            raise ValueError('Crossed order book')
        spread = (ask-bid)/((ask+bid)/2)*100
        result['futures_spread_quality'] = check(spread <= config.maximum_spread_percent,
            {'spread_percent': spread, 'maximum': config.maximum_spread_percent, 'bid': bid, 'ask': ask,
             'quote_age_seconds': age}, 'TIGHT_FUTURES_SPREAD_REQUIRED')
        try:
            lot_value = float(data['contract']['lot_size'])
            if not isfinite(lot_value) or lot_value <= 0 or not lot_value.is_integer():
                raise ValueError('Invalid lot size')
            buy_lots, sell_lots = sum(q for _, q in bids)/lot_value, sum(q for _, q in asks)/lot_value
            result['futures_depth_quality'] = check(min(buy_lots, sell_lots) >= config.minimum_depth_lots,
                {'bid_lots': buy_lots, 'ask_lots': sell_lots, 'minimum_each_side': config.minimum_depth_lots,
                 'bid_quantity': sum(q for _, q in bids), 'ask_quantity': sum(q for _, q in asks),
                 'bid_levels': len(bids), 'ask_levels': len(asks), 'quote_age_seconds': age},
                'TWO_SIDED_FUTURES_DEPTH_REQUIRED')
        except (KeyError, TypeError, ValueError, OverflowError):
            unavailable(['futures_depth_quality'], 'FUTURES_LOT_SIZE_INVALID')
        try:
            spot = float(data['spot_price'])
            obstacle = float(levels['resistance' if bullish else 'support'])
            if not isfinite(spot) or not isfinite(obstacle) or spot <= 0 or obstacle <= 0:
                raise ValueError('Invalid underlying price or level')
            space = (obstacle/spot-1)*100 if bullish else (1-obstacle/spot)*100
            result['futures_target_space_quality'] = check(space >= config.target_percent+spread,
                {'underlying_space_percent': space, 'target_percent': config.target_percent,
                 'spread_percent': spread}, 'UNDERLYING_TARGET_SPACE_PROXY_AFTER_FUTURES_SPREAD')
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            unavailable(['futures_target_space_quality'], 'UNDERLYING_TARGET_LEVELS_MISSING_OR_INVALID')
    except (KeyError, TypeError, ValueError):
        unavailable(depth_keys, 'FUTURES_DEPTH_MISSING_OR_INVALID')

    daily = None
    try:
        daily = clean(normalise_candles(data.get('daily'), 'day', now))
        daily = daily.loc[daily.index.date < now.date()]
        previous_session = now.normalize() - pd.Timedelta(days=1)
        while previous_session.weekday() >= 5 or previous_session.date().isoformat() in holidays:
            previous_session -= pd.Timedelta(days=1)
        if daily.empty or daily.index[-1].date() != previous_session.date():
            raise ValueError('Daily history missing/stale')
    except (ValueError, TypeError, KeyError, IndexError):
        daily = None

    # OI requires only a fresh quote and the previous completed daily OI/close.
    # Missing intraday history or too few ATR candles must not suppress it.
    try:
        if daily is None:
            raise ValueError('No OI baseline')
        oi, prior_oi = float(quote['oi']), float(daily.iloc[-1]['OI'])
        prev = float(daily.Close.iloc[-1])
        if not all(isfinite(v) and v > 0 for v in [oi, prior_oi, prev]):
            raise ValueError('Invalid OI baseline')
        pc, oc = (price/prev-1)*100, (oi/prior_oi-1)*100
        regime = ('LONG_BUILDUP' if pc > 0 and oc > 0 else
                  'SHORT_BUILDUP' if pc < 0 and oc > 0 else
                  'SHORT_COVERING' if pc > 0 and oc < 0 else
                  'LONG_UNWINDING' if pc < 0 and oc < 0 else 'UNCHANGED')
        result['futures_oi_quality'] = check((pc > 0 if bullish else pc < 0) and oc > 0,
            {'price_change_percent': pc, 'oi_change_percent': oc, 'current_oi': oi,
             'previous_session_oi': prior_oi, 'previous_session_close': prev,
             'baseline_timestamp': daily.index[-1].isoformat(), 'quote_age_seconds': age}, regime)
    except (ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
        unavailable(['futures_oi_quality'], 'FUTURES_OI_BASELINE_MISSING_OR_INVALID')

    intra_keys = ['futures_atr_quality', 'futures_vwap_quality', 'futures_rvol_quality',
                  'futures_adx_quality', 'futures_rsi_quality', 'futures_ema_quality',
                  'futures_supertrend_quality', 'futures_gap_quality']
    try:
        intra = clean(normalise_candles(data.get('intraday'), '5minute', now))
        # Use only fully completed bars. Never use the still-forming candle.
        intra = intra.loc[(intra.index+pd.Timedelta(minutes=5) <= now)
                          & (intra.index.time >= datetime.strptime('09:15', '%H:%M').time())
                          & (intra.index.time < datetime.strptime('15:30', '%H:%M').time())]
        session = intra.loc[intra.index.date == now.date()]
        if session.empty:
            raise ValueError('No completed session candles')
        expected = pd.date_range(now.normalize()+pd.Timedelta(hours=9, minutes=15),
                                 session.index[-1], freq='5min')
        if (completed_session_check(intra, now)['status'] != 'PASS' or not session.index.equals(expected)
                or (now-session.index[-1]-pd.Timedelta(minutes=5)).total_seconds() > config.maximum_candle_age_seconds):
            raise ValueError('Stale or incomplete session candles')
    except (ValueError, TypeError, KeyError, IndexError):
        unavailable(intra_keys, 'FUTURES_INTRADAY_MISSING_STALE_OR_INCOMPLETE')
        return finish()

    bar_evidence = {'candle_timestamp': session.index[-1].isoformat(),
                    'completed_session_bars': len(session)}
    vwap = session_vwap_quality(session, price, now=now.to_pydatetime())
    if vwap.status != 'UNKNOWN':
        vwap = check(price >= vwap.factors['vwap'] if bullish else price <= vwap.factors['vwap'],
                     {**vwap.factors, **bar_evidence},
                     'PRICE_AT_OR_ABOVE_FUTURES_VWAP' if bullish else 'PRICE_AT_OR_BELOW_FUTURES_VWAP')
        vwap = replace(vwap, warnings=['VWAP is estimated from completed futures five-minute HLC3 candles.'])
    result['futures_vwap_quality'] = vwap

    cutoff = session.index[-1].time()
    baseline = intra.loc[(intra.index.date < now.date()) & (intra.index.time <= cutoff)]
    expected_times = list(session.index.strftime('%H:%M'))
    groups = [g for _, g in baseline.groupby(baseline.index.date)
              if list(g.index.strftime('%H:%M')) == expected_times]
    totals = [float(g.Volume.sum()) for g in groups[-20:]]
    if len(totals) >= config.minimum_volume_sessions and sum(totals) > 0:
        avg = sum(totals)/len(totals)
        rv = float(session.Volume.sum())/avg
        result['futures_rvol_quality'] = check(rv >= config.minimum_rvol,
            {'rvol': rv, 'minimum': config.minimum_rvol, 'matched_average_volume': avg,
             'current_completed_volume': float(session.Volume.sum()), 'baseline_sessions': len(totals),
             **bar_evidence}, 'MATCHED_TIME_FUTURES_RVOL')
    else:
        unavailable(['futures_rvol_quality'], 'FUTURES_MATCHED_VOLUME_BASELINE_INSUFFICIENT')

    ia, adx, rsi = indicators(intra)
    if daily is not None and len(daily) >= 14 and isfinite(float(ia.iloc[-1])):
        da, _, _ = indicators(daily)
        dp, ip = float(da.iloc[-1])/price*100, float(ia.iloc[-1])/price*100
        result['futures_atr_quality'] = check(
            config.minimum_daily_atr_percent <= dp <= config.maximum_daily_atr_percent
            and config.minimum_five_minute_atr_percent <= ip <= config.maximum_five_minute_atr_percent,
            {'daily_atr_14': float(da.iloc[-1]), 'daily_atr_percent': dp,
             'five_minute_atr_14': float(ia.iloc[-1]), 'five_minute_atr_percent': ip,
             'minimum_daily_atr_percent': config.minimum_daily_atr_percent,
             'maximum_daily_atr_percent': config.maximum_daily_atr_percent,
             'minimum_five_minute_atr_percent': config.minimum_five_minute_atr_percent,
             'maximum_five_minute_atr_percent': config.maximum_five_minute_atr_percent,
             'daily_candle_timestamp': daily.index[-1].isoformat(), **bar_evidence},
            'ATR_WITHIN_CONFIGURED_VOLATILITY_BANDS')
    else:
        unavailable(['futures_atr_quality'], 'FUTURES_ATR_HISTORY_INSUFFICIENT_OR_STALE')
    av, rv = float(adx.iloc[-1]), float(rsi.iloc[-1])
    if isfinite(av):
        result['futures_adx_quality'] = check(av > config.minimum_adx,
            {'adx_14': av, 'minimum_exclusive': config.minimum_adx, **bar_evidence}, 'ADX_TREND_STRENGTH')
    else:
        unavailable(['futures_adx_quality'], 'FUTURES_ADX_HISTORY_INSUFFICIENT')
    if isfinite(rv):
        result['futures_rsi_quality'] = check(50 < rv < 70 if bullish else 30 < rv < 50,
            {'rsi_14': rv, 'minimum_exclusive': 50 if bullish else 30,
             'maximum_exclusive': 70 if bullish else 50, **bar_evidence}, 'DIRECTIONAL_RSI_WITHOUT_EXTREME')
    else:
        unavailable(['futures_rsi_quality'], 'FUTURES_RSI_HISTORY_INSUFFICIENT')
    if len(intra) >= 21:
        e9, e21 = float(intra.Close.ewm(span=9, adjust=False).mean().iloc[-1]), float(intra.Close.ewm(span=21, adjust=False).mean().iloc[-1])
        result['futures_ema_quality'] = check(price > e9 > e21 if bullish else price < e9 < e21,
            {'ema_9': e9, 'ema_21': e21, 'price': price, **bar_evidence}, 'FIVE_MINUTE_EMA_DIRECTION')
    side, line = supertrend(intra, ia, config.supertrend_multiplier)
    if line is not None and isfinite(line):
        result['futures_supertrend_quality'] = check(side == (1 if bullish else -1),
            {'supertrend': line, 'direction': side, 'atr_period': 14,
             'multiplier': config.supertrend_multiplier, **bar_evidence}, 'FIVE_MINUTE_SUPERTREND_CONFIRMATION')
    if daily is not None:
        prev = float(daily.Close.iloc[-1])
        opening = float(session.Open.iloc[0])
        gap = (opening/prev-1)*100
        follow = price >= opening if bullish else price <= opening
        result['futures_gap_quality'] = check(abs(gap) <= config.maximum_gap_percent and follow,
            {'opening_gap_percent': gap, 'opening_price': opening, 'current_price': price,
             'maximum_gap_percent': config.maximum_gap_percent}, 'GAP_SIZE_AND_DIRECTIONAL_FOLLOW_THROUGH')
    return finish()
