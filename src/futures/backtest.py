"""Intraday futures simulation with prefix-only signals and a chronological holdout."""
from collections import defaultdict
from statistics import median
import pandas as pd

from src.futures.config import FuturesScanConfig
from src.futures.costs import FuturesCosts
from src.futures.scoring import directional_setup, prepare
from src.futures.sessions import normalise_candles, CALCULATION_VERSION


def metrics(trades, minimum_samples):
    total = len(trades)
    targets = [trade for trade in trades if trade['outcome'] == 'TARGET_FIRST']
    wins = sum(max(0, trade['net_pnl']) for trade in trades)
    losses = abs(sum(min(0, trade['net_pnl']) for trade in trades))
    equity = peak = drawdown = 0
    for trade in sorted(trades, key=lambda trade: trade['exit_timestamp']):
        equity += trade['net_pnl']
        peak = max(peak, equity)
        drawdown = max(drawdown, peak-equity)
    return {'historical_signals': total, 'sample_status': 'SUFFICIENT' if total >= minimum_samples else 'INSUFFICIENT',
            'target_first_percent': len(targets)/total*100 if total >= minimum_samples else None,
            'observed_target_first_percent': len(targets)/total*100 if total else None,
            'stop_first_percent': sum(t['outcome'] == 'STOP_FIRST' for t in trades)/total*100 if total else None,
            'neither_percent': sum(t['outcome'] == 'INTRADAY_EXIT' for t in trades)/total*100 if total else None,
            'median_minutes_to_target': median(t['minutes_held'] for t in targets) if targets else None,
            'maximum_adverse_excursion_percent': max((t['mae_percent'] for t in trades), default=None),
            'maximum_favorable_excursion_percent': max((t['mfe_percent'] for t in trades), default=None),
            'average_net_pnl': sum(t['net_pnl'] for t in trades)/total if total else None,
            'profit_factor': wins/losses if losses else None,
            'profit_factor_status': 'NO_LOSSES' if total and not losses else 'AVAILABLE' if total else 'NO_SIGNALS',
            'maximum_drawdown': drawdown if total else None}


class FuturesIntradayBacktester:
    def __init__(self, config=None, costs=None):
        self.config = config or FuturesScanConfig.from_env()
        self.costs = costs or FuturesCosts.from_env()

    def run(self, candles, lot_size, *, signal_factory=None, daily_history=None, benchmark_history=None, underlying_candles=None):
        if int(lot_size) != lot_size or lot_size <= 0:
            raise ValueError('Historical contract lot size must be a positive integer')
        if 'tradingsymbol' in candles and candles.tradingsymbol.nunique() != 1:
            raise ValueError('Backtest requires one exact futures contract, not mixed expiry history')
        frame = prepare(normalise_candles(candles, '5minute'))
        # Optional underlying candles are context only; Futures barriers never use spot prices.
        daily_prepared = prepare(daily_history) if daily_history is not None and not daily_history.empty else None
        benchmark_prepared = prepare(benchmark_history) if benchmark_history is not None and not benchmark_history.empty else None
        sessions = sorted(set(frame.index.date))
        split = max(1, int(len(sessions)*(1-self.config.out_of_sample_fraction)))
        holdout = set(sessions[split:])
        trades = []
        occupied_until = {'LONG': -1, 'SHORT': -1}
        signal_factory = signal_factory or self._signals
        for index in range(self.config.minimum_history-1, len(frame)-1):
            stamp = frame.index[index]
            next_stamp = frame.index[index+1]
            if (stamp.date() != next_stamp.date() or
                    next_stamp != stamp + pd.Timedelta(minutes=5) or
                    not (9, 20) <= (next_stamp.hour, next_stamp.minute) < (self.config.entry_cutoff_hour, self.config.entry_cutoff_minute)):
                continue
            prefix = frame.iloc[:index+1].copy()
            kwargs = {}
            if daily_prepared is not None:
                completed = daily_prepared.loc[daily_prepared.index.date < stamp.date()]
                if not completed.empty:
                    kwargs['daily_atr'] = float(completed.ATR.iloc[-1])
            if benchmark_prepared is not None:
                past = benchmark_prepared.loc[benchmark_prepared.index.date < stamp.date()]
                if len(past) >= 200:
                    row = past.iloc[-1]
                    kwargs['market_regime'] = ('BULLISH' if row.Close > row.EMA50 > row.EMA200 else
                                              'BEARISH' if row.Close < row.EMA50 < row.EMA200 else 'SIDEWAYS')
            signals = self._signals(prefix, **kwargs) if signal_factory == self._signals else signal_factory(prefix)
            for signal in signals:
                side = signal.get('side')
                if side not in occupied_until or index <= occupied_until[side]:
                    continue
                if signal.get('confirmation_timestamp') not in (None, stamp, stamp.isoformat()):
                    continue
                trade = self.simulate(frame, index+1, side, signal.get('setup_type', 'UNVERIFIED'), lot_size,
                    entry_trigger=signal.get('entry_trigger'), invalidation=signal.get('setup_invalidation_price'))
                if trade:
                    trade['partition'] = 'OUT_OF_SAMPLE' if stamp.date() in holdout else 'IN_SAMPLE'
                    trade['signal_timestamp'] = stamp.isoformat()
                    trade['market_regime'] = signal.get('market_regime', 'UNKNOWN')
                    trades.append(trade)
                    occupied_until[side] = trade.pop('_exit_index')
        grouped = defaultdict(list)
        for trade in trades:
            for field in ('side', 'setup_type', 'partition', 'time_of_day', 'market_regime'):
                grouped[f'{field}:{trade[field]}'].append(trade)
            grouped[f"side_setup:{trade['side']}:{trade['setup_type']}:{trade['partition']}"].append(trade)
        return {'metrics': metrics(trades, self.config.minimum_historical_signals),
                'movement_basis': 'FUTURES', 'calculation_version': CALCULATION_VERSION,
                'underlying_candles_role': 'RESEARCH_ONLY',
                'groups': {key: metrics(items, self.config.minimum_historical_signals) for key, items in grouped.items()},
                'trades': trades, 'holdout_start': min(holdout).isoformat() if holdout else None,
                'validation_status': 'OUT_OF_SAMPLE_EVALUATED' if any(t['partition'] == 'OUT_OF_SAMPLE' for t in trades) else 'INSUFFICIENT_OUT_OF_SAMPLE_SIGNALS',
                'historical_basis': 'Exact-contract futures OHLCV; technical signals only. Historical news/OI/depth gates are not reconstructed.',
                'time_and_excursion_resolution': 'Five-minute OHLC: time-to-target uses the exit-bar end; excursions include the full exit bar conservatively.',
                'cost_assumptions': self.costs.__dict__, 'ambiguous_candle_policy': 'STOP_FIRST',
                'position_policy': 'One non-overlapping hypothetical position per direction; LONG/SHORT tested independently'}

    @property
    def exit_time(self):
        return self.config.intraday_exit_hour, self.config.intraday_exit_minute

    def _signals(self, prefix, market_regime='UNKNOWN', **kwargs):
        results = []
        for side in ('LONG', 'SHORT'):
            setup = directional_setup(prefix, side, config=self.config, prepared=True, **kwargs)
            if (setup['technical_score'] is not None and setup['technical_score'] >= self.config.minimum_score
                    and setup['timing'] == 'READY' and setup.get('confirmed')):
                results.append({'side': side, 'setup_type': setup['setup_type'], 'market_regime': market_regime,
                    'entry_trigger': setup['evidence'].get('short_trigger_price') if side == 'SHORT' else None,
                    'setup_invalidation_price': setup['evidence'].get('setup_invalidation_price') if side == 'SHORT' else None})
        return results

    def simulate(self, frame, start, side, setup_type, lot_size, *, entry_trigger=None, invalidation=None, underlying=None):
        # Positional start is used internally; reject invalid direct-call session data rather than shift it.
        normalized = normalise_candles(frame, '5minute')
        if not normalized.index.equals(frame.index):
            raise ValueError('SIMULATION_REQUIRES_NORMALIZED_SESSION_CANDLES')
        # Kept in the public signature for compatibility; underlying is research
        # context and cannot alter a Futures entry, barrier, or outcome.
        entry_stamp = frame.index[start]
        if not (9, 20) <= (entry_stamp.hour, entry_stamp.minute) < (self.config.entry_cutoff_hour, self.config.entry_cutoff_minute):
            return None
        entry = float(frame.iloc[start].Open)
        fill_basis = 'NEXT_BAR_OPEN'
        if side == 'SHORT' and entry_trigger is not None:
            if entry > entry_trigger:
                if float(frame.iloc[start].Low) > entry_trigger:
                    return None
                entry, fill_basis = float(entry_trigger), 'TRIGGER_TOUCHED_IN_NEXT_BAR'
            if (entry_trigger-entry)/entry_trigger*100 > self.config.short_maximum_trigger_distance_percent:
                return None
        sign = 1 if side == 'LONG' else -1
        target, stop = entry*(1+sign*self.config.target_fraction), entry*(1-sign*self.config.stop_fraction)
        if side == 'SHORT' and invalidation is not None and stop <= invalidation:
            return None
        mae = mfe = 0
        outcome, exit_price, exit_index = 'INTRADAY_EXIT', entry, start
        for index in range(start, len(frame)):
            stamp, row = frame.index[index], frame.iloc[index]
            if stamp.date() != entry_stamp.date() or (stamp.hour, stamp.minute) >= self.exit_time:
                # Exit at the configured bar's open, before considering its future range.
                if stamp.date() == entry_stamp.date():
                    exit_price, exit_index = float(row.Open), index
                break
            if index > start and stamp != frame.index[index-1] + pd.Timedelta(minutes=5):
                return None  # A gap prevents determining which barrier hit first.
            adverse = entry-float(row.Low) if side == 'LONG' else float(row.High)-entry
            favourable = float(row.High)-entry if side == 'LONG' else entry-float(row.Low)
            mae, mfe = max(mae, adverse/entry*100), max(mfe, favourable/entry*100)
            stop_hit = float(row.Low) <= stop if side == 'LONG' else float(row.High) >= stop
            target_hit = float(row.High) >= target if side == 'LONG' else float(row.Low) <= target
            exit_index, exit_price = index, float(row.Close)
            if stop_hit:
                outcome = 'STOP_FIRST'
                exit_price = min(stop, float(row.Open)) if side == 'LONG' else max(stop, float(row.Open))
                break
            if target_hit:
                outcome, exit_price = 'TARGET_FIRST', target
                break
        else:
            return None  # Incomplete final session cannot establish an intraday exit.
        if outcome == 'INTRADAY_EXIT' and (frame.index[exit_index].hour, frame.index[exit_index].minute) < self.exit_time:
            return None
        pnl = self.costs.round_trip(entry, exit_price, lot_size, side, entry_stamp.date())
        return {'side': side, 'setup_type': setup_type, 'entry': entry, 'target': target, 'stop_loss': stop,
                'movement_basis': 'FUTURES', 'underlying_entry': None,
                'exit_fill_basis': 'BARRIER_PRICE_WITH_GAP_HANDLING',
                'entry_trigger': entry_trigger, 'entry_fill_basis': fill_basis,
                'exit': exit_price, 'outcome': outcome, **pnl, 'quantity': lot_size,
                'entry_timestamp': entry_stamp.isoformat(), 'exit_timestamp': frame.index[exit_index].isoformat(),
                'minutes_held': (frame.index[exit_index]-entry_stamp).total_seconds()/60 + (0 if outcome == 'INTRADAY_EXIT' else 5),
                'time_of_day': f'{entry_stamp.hour:02d}:00', 'mae_percent': mae, 'mfe_percent': mfe,
                '_exit_index': exit_index}
