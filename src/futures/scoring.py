"""Prefix-only directional evidence; LONG and SHORT scores are independent."""
from math import isfinite
import pandas as pd

from src.quality.futures_execution import clean, indicators, wilder
from src.futures.config import FuturesScanConfig
from src.indicators.volume import VolumeIndicator
from src.futures.sessions import normalise_candles


def prepare(frame):
    frame = clean(normalise_candles(frame))
    if frame.empty:
        raise ValueError('NO_VALID_EXCHANGE_SESSION_CANDLES')
    for period in (9, 21, 50, 200):
        frame[f'EMA{period}'] = frame.Close.ewm(span=period, adjust=False).mean()
    frame['ATR'], frame['ADX'], frame['RSI'] = indicators(frame)
    frame['MACD'] = frame.Close.ewm(span=12, adjust=False).mean() - frame.Close.ewm(span=26, adjust=False).mean()
    frame['MACD_SIGNAL'] = frame.MACD.ewm(span=9, adjust=False).mean()
    frame['MACD_HISTOGRAM'] = frame.MACD - frame.MACD_SIGNAL
    previous = frame.Close.shift()
    tr = pd.concat([frame.High-frame.Low, (frame.High-previous).abs(), (frame.Low-previous).abs()], axis=1).max(axis=1)
    atr = wilder(tr)
    up, down = frame.High.diff(), -frame.Low.diff()
    frame['PLUS_DI'] = (wilder(up.where((up > down) & (up > 0), 0)) / atr * 100).mask(atr == 0, 0)
    frame['MINUS_DI'] = (wilder(down.where((down > up) & (down > 0), 0)) / atr * 100).mask(atr == 0, 0)
    frame['RVOL'] = frame.Volume / frame.Volume.shift().rolling(20).mean()
    frame['RVOL_BASIS'] = 'PRIOR_20_BARS'
    if bool(frame.iloc[-1].get('IS_LIVE_CANDLE', False)):
        volume = VolumeIndicator.calculate(frame)
        frame.loc[frame.index[-1], 'RVOL'] = volume.RVOL.iloc[-1]
        frame.loc[frame.index[-1], 'RVOL_BASIS'] = 'EXISTING_ELAPSED_SESSION_VOLUME_PIPELINE'
    return frame


def relative_returns(stock, other, periods=20):
    if other is None or other.empty:
        return None
    other = clean(normalise_candles(other))
    aligned = pd.concat([stock.Close.rename('stock'), other.Close.rename('other')], axis=1).dropna()
    if len(aligned) < periods + 1:
        return None
    sr = (aligned.stock.iloc[-1] / aligned.stock.iloc[-periods-1] - 1) * 100
    br = (aligned.other.iloc[-1] / aligned.other.iloc[-periods-1] - 1) * 100
    return {'stock_return_percent': float(sr), 'benchmark_return_percent': float(br),
            'excess_percentage_points': float(sr-br), 'sessions': periods,
            'as_of': aligned.index[-1].isoformat()}


def directional_setup(frame, side, *, config=None, benchmark=None, sector=None, daily_atr=None,
                      prepared=False):
    config = config or FuturesScanConfig.from_env()
    if side not in {'LONG', 'SHORT'}:
        raise ValueError('side must be LONG or SHORT')
    frame = frame if prepared else prepare(frame)
    if len(frame) < config.minimum_history:
        return {'side': side, 'technical_score': None, 'setup_type': 'UNVERIFIED',
                'timing': 'UNKNOWN', 'reason_codes': ['INSUFFICIENT_EMA200_HISTORY'], 'evidence': {}}
    row, prev = frame.iloc[-1], frame.iloc[-2]
    sign = 1 if side == 'LONG' else -1
    close = float(row.Close)
    atr = float(daily_atr if daily_atr is not None else row.ATR)
    if not isfinite(atr) or atr <= 0:
        return {'side': side, 'technical_score': None, 'setup_type': 'UNVERIFIED',
                'timing': 'UNKNOWN', 'reason_codes': ['ATR_UNAVAILABLE'], 'evidence': {}}
    emAs = [float(row[f'EMA{period}']) for period in (9, 21, 50, 200)]
    below_above = [(close-value)*sign > 0 for value in emAs]
    alignment = [(emAs[i]-emAs[i+1])*sign > 0 for i in range(3)]
    recent, prior = frame.iloc[-4:], frame.iloc[-8:-4]
    structure = (recent.High.max()-prior.High.max())*sign > 0 and (recent.Low.min()-prior.Low.min())*sign > 0
    price_level = float(frame.Low.iloc[-21:-1].min() if side == 'SHORT' else frame.High.iloc[-21:-1].max())
    broken = (close-price_level)*sign > 0
    failed_retest = (float(prev.Close)-price_level)*sign > 0 and (
        float(row.Low) <= price_level if side == 'LONG' else float(row.High) >= price_level) and (close-price_level)*sign > 0
    rejection = ((float(row.Low) <= min(emAs[:2]) if side == 'LONG' else float(row.High) >= max(emAs[:2]))
                 and (close-float(row.Open))*sign > 0 and (close-emAs[0])*sign > 0)
    short_break_age, short_trigger, short_invalidation = None, None, None
    fresh_break = False
    if side == 'SHORT':
        # Find the actual prior support that was crossed, rather than relabelling
        # every lower close as a new breakdown. Each anchor is prefix-only.
        break_index, break_level = None, None
        for index in range(max(21, len(frame)-8), len(frame)):
            level = float(frame.Low.iloc[index-20:index].min())
            if break_index is None and float(frame.Close.iloc[index]) < level <= float(frame.Close.iloc[index-1]):
                break_index, break_level = index, level
        if break_index is not None:
            short_break_age = len(frame)-1-break_index
            fresh_break = short_break_age < config.short_fresh_breakdown_bars and close < break_level
            if fresh_break:
                price_level = break_level
        tolerance = atr*config.short_retest_tolerance_atr
        pullback_seen = float(prev.Close) > float(frame.Close.iloc[-3]) or float(prev.Close) > float(prev.Open)
        retest = (break_index is not None and short_break_age > 0
                  and float(row.High) >= break_level-tolerance and close < break_level
                  and close < float(row.Open) and pullback_seen)
        ema_touch = float(row.High) >= min(emAs[:2])-tolerance
        vwap_touch = False  # Added below once current-session VWAP is known.
        failed_retest = bool(retest)
        rejection = bool(ema_touch and close < min(emAs[:2]) and close < float(row.Open) and pullback_seen)
    engulfing = ((float(prev.Close)-float(prev.Open))*sign < 0
                 and (close-float(row.Open))*sign > 0
                 and (float(row.Open)-float(prev.Close))*sign <= 0
                 and (close-float(prev.Open))*sign >= 0)
    recent_rs, prior_rs = float(recent.RSI.min()), float(prior.RSI.min())
    bullish_divergence = recent.Low.min() < prior.Low.min() and recent_rs > prior_rs
    bearish_divergence = recent.High.max() > prior.High.max() and recent.RSI.max() < prior.RSI.max()
    reversal = engulfing or (bearish_divergence if side == 'SHORT' else bullish_divergence)
    rsi = float(row.RSI)
    rsi_good = config.bullish_rsi_low <= rsi <= config.bullish_rsi_high if side == 'LONG' else config.bearish_rsi_low <= rsi <= config.bearish_rsi_high
    histogram = float(row.MACD_HISTOGRAM)
    crossover = histogram*sign > 0 and float(prev.MACD_HISTOGRAM)*sign <= 0
    momentum = 40*int(rsi_good) + 25*int(histogram*sign > 0) + 15*int(crossover or (histogram-float(prev.MACD_HISTOGRAM))*sign > 0)
    di_aligned = float(row.PLUS_DI-row.MINUS_DI)*sign > 0
    momentum += 20*int(float(row.ADX) > config.minimum_adx and di_aligned)
    rvol = float(row.RVOL) if isfinite(float(row.RVOL)) else None
    pressure = float(((frame.Close.iloc[-5:]-frame.Open.iloc[-5:])*sign > 0).mean())
    last_five = frame.iloc[-5:]
    directional_volume = float(last_five.loc[(last_five.Close-last_five.Open)*sign > 0, 'Volume'].sum())
    pressure_volume_share = directional_volume/float(last_five.Volume.sum()) if last_five.Volume.sum() > 0 else 0
    session_date = frame.index[-1].date()
    session = frame.loc[frame.index.date == session_date]
    intraday = len(session) > 1
    volume = float(session.Volume.sum())
    vwap = float((((session.High+session.Low+session.Close)/3)*session.Volume).sum()/volume) if intraday and volume > 0 else None
    if side == 'SHORT':
        vwap_touch = vwap is not None and float(row.High) >= vwap-tolerance and close < vwap and close < float(row.Open) and pullback_seen
        rejection = rejection or vwap_touch or failed_retest
    # A wick from the immediately preceding bar is not automatically a meaningful
    # obstacle. Use confirmed swing levels, with two later completed candles.
    window = frame.tail(200)
    lows = window.Low[window.Low == window.Low.rolling(5, center=True).min()]
    highs = window.High[window.High == window.High.rolling(5, center=True).max()]
    support = float(lows[lows < close].max()) if (lows < close).any() else None
    resistance = float(highs[highs > close].min()) if (highs > close).any() else None
    obstacle = resistance if side == 'LONG' else support
    space = (obstacle-close)*sign/close*100 if obstacle is not None else None
    market_rs, sector_rs = relative_returns(frame, benchmark), relative_returns(frame, sector)
    sector_market = relative_returns(sector, benchmark) if sector is not None and not sector.empty else None
    sector_checks = [value['excess_percentage_points']*sign > 0 for value in (market_rs, sector_rs, sector_market) if value]
    move = (close/float(session.Open.iloc[0])-1)*100 if intraday else (close/float(prev.Close)-1)*100
    consumed = float(session.High.max()-session.Low.min())/atr if intraday else float(row.High-row.Low)/atr
    extension = (close-emAs[1])*sign/atr
    vwap_distance = (close/vwap-1)*100*sign if vwap else None
    span = float(row.High-row.Low)
    lower_upper_wick = (min(float(row.Open), close)-float(row.Low)) if side == 'SHORT' else (float(row.High)-max(float(row.Open), close))
    exhaustion = span > 0 and lower_upper_wick/span >= .5
    reasons = []
    timing = 'READY'
    if side == 'SHORT' and rsi < config.short_oversold_rsi:
        timing = 'TOO LATE'
        reasons.append('SHORT_EXCESSIVELY_OVERSOLD')
    elif move*sign > config.maximum_move_percent or consumed > config.maximum_atr_consumed+1e-9 or extension > config.maximum_extension_atr:
        timing = 'TOO LATE'
        reasons.append('MOVE_OR_ATR_ALREADY_CONSUMED')
    elif vwap_distance is not None and vwap_distance > config.maximum_vwap_extension_percent:
        timing = 'WAIT FOR PULLBACK'
        reasons.append('TOO_FAR_FROM_VWAP')
    elif exhaustion or (bullish_divergence if side == 'SHORT' else bearish_divergence):
        timing = 'WAIT FOR PULLBACK'
        reasons.append('EXHAUSTION_OR_COUNTERTREND_DIVERGENCE')
    if space is None:
        reasons.append('NEXT_PRICE_OBSTACLE_UNVERIFIED')
    elif space < config.target_fraction*100:
        if timing not in {'TOO LATE', 'WAIT FOR PULLBACK'}:
            timing = 'WAIT'
        reasons.append('INSUFFICIENT_REMAINING_TARGET_SPACE')
    components = {'trend': (sum(below_above)/4*.45 + sum(alignment)/3*.35 + int(structure)*.20)*100,
                  'momentum': momentum,
                  'volume': (min(1, rvol/config.minimum_rvol)*60 + pressure_volume_share*40) if rvol is not None else None,
                  'structure': 100 if broken and (rvol or 0) >= config.minimum_rvol else 85 if failed_retest or rejection else 75 if reversal else 60 if structure else 20,
                  'sector': sum(sector_checks)/len(sector_checks)*100 if sector_checks else None,
                  'opportunity': (50*int(space is not None and space >= config.target_fraction*100)
                                  + 25*int(extension <= config.maximum_extension_atr)
                                  + 25*int(consumed <= config.maximum_atr_consumed))}
    weights = config.weights('bullish' if side == 'LONG' else 'bearish')
    available_weight = sum(weights[key] for key, value in components.items() if value is not None)
    score = sum(value*weights[key] for key, value in components.items() if value is not None)/available_weight
    setup = ('SUPPORT_BREAKDOWN' if side == 'SHORT' else 'RESISTANCE_BREAKOUT') if broken else (
        'PULLBACK_REJECTION' if rejection or failed_retest else
        ('BEARISH_REVERSAL' if side == 'SHORT' else 'BULLISH_REVERSAL') if reversal else
        ('SECTOR_SELLING_PRESSURE' if side == 'SHORT' else 'SECTOR_BUYING_PRESSURE')
        if sector_checks and all(sector_checks) and sum(alignment) < 3 else
        ('BEARISH_TREND_CONTINUATION' if side == 'SHORT' else 'BULLISH_TREND_CONTINUATION')
        if sum(alignment) >= 2 and histogram*sign > 0 else 'APPROACHING_TRIGGER')
    confirmed = (close-float(row.Open))*sign > 0 and histogram*sign > 0 and (broken or rejection or reversal or sum(alignment) >= 2)
    if side == 'SHORT':
        if failed_retest or rejection:
            setup = 'PULLBACK_REJECTION'
            short_trigger = float(row.Low)
            short_invalidation = float(row.High)
            confirmed = close < float(row.Open) and close < float(prev.Close) and histogram < 0
        elif fresh_break:
            setup = 'SUPPORT_BREAKDOWN'
            short_trigger = float(price_level)
            short_invalidation = float(price_level)
            confirmed = close < float(price_level) and close < float(row.Open) and (rvol or 0) >= config.minimum_rvol
        elif sum(alignment) >= 2 and histogram < 0:
            setup = 'BEARISH_TREND_CONTINUATION'
            short_trigger = float(row.Low)
            short_invalidation = float(row.High)
            confirmed = structure and close < float(prev.Close) and close < float(row.Open)
        else:
            short_trigger = float(row.Low)
            short_invalidation = float(row.High)
        if broken and not fresh_break and not rejection:
            reasons.append('BREAKDOWN_ALREADY_UNDERWAY_NOT_FRESH')
    if not confirmed and timing == 'READY':
        timing = 'WAIT'
        reasons.append('SETUP_CONFIRMATION_PENDING')
    evidence = {'price': close, 'rsi_14': rsi, 'macd': float(row.MACD), 'macd_signal': float(row.MACD_SIGNAL),
                'macd_histogram': histogram, 'macd_crossover': crossover, 'adx_14': float(row.ADX),
                'plus_di': float(row.PLUS_DI), 'minus_di': float(row.MINUS_DI),
                'ema_values': dict(zip(('EMA9', 'EMA21', 'EMA50', 'EMA200'), emAs)),
                'ema_alignment': 'ALIGNED' if all(alignment) else 'MIXED', 'price_on_directional_side_of_emas': below_above,
                'lower_highs_lower_lows' if side == 'SHORT' else 'higher_highs_higher_lows': bool(structure),
                'relative_volume': rvol, 'directional_candles_fraction': pressure, 'vwap': vwap,
                'relative_volume_basis': row.get('RVOL_BASIS', 'PRIOR_20_BARS'),
                'directional_candle_volume_share': pressure_volume_share,
                'vwap_relationship': 'ABOVE' if vwap and close > vwap else 'BELOW' if vwap else 'UNKNOWN',
                'support': support, 'resistance': resistance, 'trigger': price_level, 'target_space_percent': space,
                'daily_atr': atr, 'atr_consumed_fraction': consumed, 'move_percent': move,
                'ema21_extension_atr': extension, 'vwap_distance_percent': vwap_distance,
                'selling_or_buying_exhaustion': exhaustion, 'bullish_rsi_divergence': bool(bullish_divergence),
                'bearish_rsi_divergence': bool(bearish_divergence), 'market_relative': market_rs,
                'sector_relative': sector_rs, 'sector_vs_nifty': sector_market,
                'pattern': ('BULLISH_ENGULFING' if side == 'LONG' else 'BEARISH_ENGULFING') if engulfing else 'NONE',
                'signal_timestamp': frame.index[-1].isoformat()}
    if side == 'SHORT':
        evidence.update({'short_trigger_price': short_trigger, 'setup_invalidation_price': short_invalidation,
                         'breakdown_age_bars': short_break_age, 'fresh_support_breakdown': bool(fresh_break),
                         'pullback_rejection_confirmed': bool(confirmed and setup == 'PULLBACK_REJECTION'),
                         'oversold_rsi_floor': config.short_oversold_rsi})
    return {'side': side, 'technical_score': round(score, 2), 'score_components': components,
            'score_coverage_percent': round(available_weight*100, 2), 'setup_type': setup,
            'timing': timing, 'confirmed': bool(confirmed), 'reason_codes': reasons, 'evidence': evidence}


def score_both(frame, **kwargs):
    frame = prepare(frame)
    return {side: directional_setup(frame, side, prepared=True, **kwargs) for side in ('LONG', 'SHORT')}
