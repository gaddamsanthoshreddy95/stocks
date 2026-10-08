"""Explainable intraday futures gates. No margin lookup or order submission."""
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
import os
from zoneinfo import ZoneInfo
import pandas as pd
from src.quality.models import QualityScore
from src.quality.futures_selection import session_vwap_quality

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
    out = pd.Series(float('nan'), index=series.index)
    if len(valid) < period:
        return out
    value = float(valid.iloc[:period].mean())
    out.loc[valid.index[period-1]] = value
    for stamp, number in valid.iloc[period:].items():
        value = (value * (period - 1) + float(number)) / period
        out.loc[stamp] = value
    return out


def indicators(frame):
    previous = frame.Close.shift()
    tr = pd.concat([frame.High-frame.Low, (frame.High-previous).abs(),
                    (frame.Low-previous).abs()], axis=1).max(axis=1)
    atr = wilder(tr)
    up, down = frame.High.diff(), -frame.Low.diff()
    plus = wilder(up.where((up > down) & (up > 0), 0)) / atr * 100
    minus = wilder(down.where((down > up) & (down > 0), 0)) / atr * 100
    dx = (plus-minus).abs() / (plus+minus) * 100
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
        stock, sector = clean(stock_history), clean(sector_history)
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
    try:
        stamp = pd.Timestamp(quote['timestamp'])
        stamp = stamp.tz_localize('Asia/Kolkata') if stamp.tz is None else stamp.tz_convert('Asia/Kolkata')
        price = float(quote['last_price'])
        if not isfinite(price) or price <= 0 or not 0 <= (now-stamp).total_seconds() <= 300 or stamp.date() != now.date():
            raise ValueError('Stale quote')
        if not (9,15) <= (now.hour,now.minute) <= (15,30):
            raise ValueError('Market closed')
    except (KeyError,TypeError,ValueError):
        return {**result, **{k:unknown('FUTURES_QUOTE_STALE_OR_INVALID') for k in CHECKS if k not in {'futures_event_quality','futures_sector_strength_quality'}}}
    try:
        depth = quote['depth']
        bids = [x for x in depth['buy'] if float(x['price']) > 0 and float(x['quantity']) > 0]
        asks = [x for x in depth['sell'] if float(x['price']) > 0 and float(x['quantity']) > 0]
        bid, ask = max(float(x['price']) for x in bids), min(float(x['price']) for x in asks)
        lot = int(data['contract']['lot_size'])
        if lot <= 0 or ask < bid or not all(isfinite(float(x[k])) for x in bids+asks for k in ['price','quantity']):
            raise ValueError('Invalid depth')
        spread = (ask-bid)/((ask+bid)/2)*100
        buy_lots, sell_lots = sum(float(x['quantity']) for x in bids)/lot, sum(float(x['quantity']) for x in asks)/lot
        result['futures_spread_quality'] = check(spread <= config.maximum_spread_percent, {'spread_percent':spread,'maximum':config.maximum_spread_percent,'bid':bid,'ask':ask}, 'TIGHT_FUTURES_SPREAD_REQUIRED')
        result['futures_depth_quality'] = check(min(buy_lots,sell_lots) >= config.minimum_depth_lots, {'bid_lots':buy_lots,'ask_lots':sell_lots,'minimum_each_side':config.minimum_depth_lots}, 'TWO_SIDED_FUTURES_DEPTH_REQUIRED')
        # Underlying levels must not be compared directly with futures prices.
        spot = float(data['spot_price'])
        obstacle = float(levels['resistance' if bullish else 'support'])
        if not isfinite(spot) or not isfinite(obstacle) or spot <= 0 or obstacle <= 0:
            raise ValueError('Invalid underlying price or level')
        space = (obstacle/spot-1)*100 if bullish else (1-obstacle/spot)*100
        result['futures_target_space_quality'] = check(space >= config.target_percent+spread, {'underlying_space_percent':space,'target_percent':config.target_percent,'spread_percent':spread}, 'UNDERLYING_TARGET_SPACE_PROXY_AFTER_FUTURES_SPREAD')
    except (KeyError,TypeError,ValueError,ZeroDivisionError):
        pass
    try:
        daily = clean(data.get('daily'))
        daily = daily.loc[daily.index.date < now.date()]
        if len(daily) < 28 or (now.date()-daily.index[-1].date()).days > 7:
            raise ValueError('Daily history missing/stale')
        intra = clean(data.get('intraday'))
        intra = intra.loc[(intra.index+pd.Timedelta(minutes=5) <= now) & (intra.index.time >= datetime.strptime('09:15','%H:%M').time()) & (intra.index.time < datetime.strptime('15:30','%H:%M').time())]
        session = intra.loc[intra.index.date == now.date()]
        if len(session) < 28 or (session.index[0].hour, session.index[0].minute) != (9,15) or (now-session.index[-1]).total_seconds() > 600 or (session.index.to_series().diff().dropna() != pd.Timedelta(minutes=5)).any():
            raise ValueError('Five-minute history missing/stale/incomplete')
        da, _, _ = indicators(daily)
        ia, adx, rsi = indicators(intra)
        dp, ip = float(da.iloc[-1])/price*100, float(ia.iloc[-1])/price*100
        result['futures_atr_quality'] = check(config.minimum_daily_atr_percent <= dp <= config.maximum_daily_atr_percent and config.minimum_five_minute_atr_percent <= ip <= config.maximum_five_minute_atr_percent, {'daily_atr_14':float(da.iloc[-1]),'daily_atr_percent':dp,'five_minute_atr_14':float(ia.iloc[-1]),'five_minute_atr_percent':ip,'minimum_daily_atr_percent':config.minimum_daily_atr_percent,'maximum_daily_atr_percent':config.maximum_daily_atr_percent,'minimum_five_minute_atr_percent':config.minimum_five_minute_atr_percent,'maximum_five_minute_atr_percent':config.maximum_five_minute_atr_percent}, 'ATR_WITHIN_CONFIGURED_VOLATILITY_BANDS')
        vwap = session_vwap_quality(session, price, now=now.to_pydatetime())
        if not bullish and vwap.status != 'UNKNOWN':
            vwap = check(price <= vwap.factors['vwap'], vwap.factors, 'PRICE_AT_OR_BELOW_FUTURES_VWAP')
        result['futures_vwap_quality'] = vwap
        cutoff = session.index[-1].time()
        baseline = intra.loc[(intra.index.date < now.date()) & (intra.index.time <= cutoff)]
        # Require complete matched clock-time buckets, not full-day averages.
        expected = list(session.index.strftime('%H:%M'))
        groups = [g for _,g in baseline.groupby(baseline.index.date) if list(g.index.strftime('%H:%M')) == expected]
        totals = [float(g.Volume.sum()) for g in groups[-20:]]
        if len(totals) >= config.minimum_volume_sessions and sum(totals) > 0:
            avg = sum(totals)/len(totals); rv = float(session.Volume.sum())/avg
            result['futures_rvol_quality'] = check(rv >= config.minimum_rvol, {'rvol':rv,'matched_average_volume':avg,'current_completed_volume':float(session.Volume.sum()),'baseline_sessions':len(totals)}, 'MATCHED_TIME_FUTURES_RVOL')
        av, rv = float(adx.iloc[-1]), float(rsi.iloc[-1])
        if isfinite(av): result['futures_adx_quality'] = check(av > config.minimum_adx, {'adx_14':av,'minimum_exclusive':config.minimum_adx}, 'ADX_TREND_STRENGTH')
        if isfinite(rv): result['futures_rsi_quality'] = check(50 < rv < 70 if bullish else 30 < rv < 50, {'rsi_14':rv}, 'DIRECTIONAL_RSI_WITHOUT_EXTREME')
        e9, e21 = float(intra.Close.ewm(span=9,adjust=False).mean().iloc[-1]), float(intra.Close.ewm(span=21,adjust=False).mean().iloc[-1])
        result['futures_ema_quality'] = check(price > e9 > e21 if bullish else price < e9 < e21, {'ema_9':e9,'ema_21':e21,'price':price}, 'FIVE_MINUTE_EMA_DIRECTION')
        side, line = supertrend(intra, ia, config.supertrend_multiplier)
        result['futures_supertrend_quality'] = check(side == (1 if bullish else -1), {'supertrend':line,'direction':side,'atr_period':14,'multiplier':config.supertrend_multiplier}, 'FIVE_MINUTE_SUPERTREND_CONFIRMATION')
        prev = float(daily.Close.iloc[-1])
        opening = float(session.Open.iloc[0]); gap = (opening/prev-1)*100
        follow = price >= opening if bullish else price <= opening
        result['futures_gap_quality'] = check(abs(gap) <= config.maximum_gap_percent and follow, {'opening_gap_percent':gap,'opening_price':opening,'current_price':price,'maximum_gap_percent':config.maximum_gap_percent}, 'GAP_SIZE_AND_DIRECTIONAL_FOLLOW_THROUGH')
        try:
            oi, prior_oi = float(quote['oi']), float(daily.iloc[-1]['OI'])
            prev = float(daily.Close.iloc[-1]); pc, oc = (price/prev-1)*100, (oi/prior_oi-1)*100
            if all(isfinite(v) for v in [oi,prior_oi,pc,oc]) and oi > 0 and prior_oi > 0:
                regime = 'LONG_BUILDUP' if pc > 0 and oc > 0 else 'SHORT_BUILDUP' if pc < 0 and oc > 0 else 'SHORT_COVERING' if pc > 0 and oc < 0 else 'LONG_UNWINDING' if pc < 0 and oc < 0 else 'UNCHANGED'
                result['futures_oi_quality'] = check((pc > 0 if bullish else pc < 0) and oc > 0, {'price_change_percent':pc,'oi_change_percent':oc,'current_oi':oi,'previous_session_oi':prior_oi}, regime)
        except (ValueError, TypeError, KeyError, ZeroDivisionError):
            pass
    except (ValueError,TypeError,KeyError,ZeroDivisionError,IndexError):
        pass
    return result
