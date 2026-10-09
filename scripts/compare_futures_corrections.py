"""Offline correction evidence using retained candles; no scan, orders, or backtest.

The raw/normalized replay isolates candle normalization under the same indicator
and scoring formulas. Saved original scores are retained separately. This is not
a full old-engine replay, an approval reassessment, or a price recommendation.
"""
import argparse
from dataclasses import fields
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sqlite3
import sys
from types import FunctionType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from src.futures import scoring
from src.futures.config import FuturesScanConfig
from src.futures.costs import FuturesCosts
from src.futures.sessions import CALCULATION_VERSION, normalise_candles


def digest(path):
    value = sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def read_frame(db, identity):
    row = db.execute("SELECT payload FROM records WHERE kind='candles' AND identity=?", (identity,)).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    return pd.DataFrame(payload['values'], columns=payload['columns'],
        index=pd.DatetimeIndex(pd.to_datetime(payload['index'], utc=True)).tz_convert('Asia/Kolkata'))


def invalid_candle_details(frame):
    """Identify rejected source fields without repairing or dropping their values."""
    if frame is None:
        return [{'reason': 'MISSING_CANDLE_FRAME'}]
    required = ['Open', 'High', 'Low', 'Close', 'Volume']
    missing = [name for name in required if name not in frame]
    if missing:
        return [{'reason': 'MISSING_FIELDS', 'fields': missing}]
    details = []
    values = frame[required].apply(pd.to_numeric, errors='coerce')
    for stamp, row in values.iterrows():
        reasons = []
        if not all(isfinite(value) for value in row):
            reasons.append('NONFINITE_OHLCV')
        else:
            if min(row.Open, row.High, row.Low, row.Close) <= 0:
                reasons.append('NONPOSITIVE_PRICE')
            if row.Volume < 0:
                reasons.append('NEGATIVE_VOLUME')
            if row.High < max(row.Open, row.Close, row.Low):
                reasons.append('HIGH_BELOW_OPEN_CLOSE_OR_LOW')
            if row.Low > min(row.Open, row.Close):
                reasons.append('LOW_ABOVE_OPEN_OR_CLOSE')
        if reasons:
            details.append({'timestamp': stamp.isoformat(), 'reason_codes': reasons,
                'fields': {name: float(value) if isfinite(value) else None for name, value in row.items()}})
    return details


def collect(report_path, cache_path):
    before_hashes = {str(path): digest(path) for path in (report_path, cache_path)}
    report = json.loads(report_path.read_text())
    now = pd.Timestamp(report['generated_at'])
    cutoff = pd.Timestamp(report['completed_session_as_of'])
    config = FuturesScanConfig(**{key: value for key, value in report['config'].items()
                                 if key in {field.name for field in fields(FuturesScanConfig)}})
    costs = FuturesCosts(**report['cost_assumptions'])
    # Deliberately change only the normalization input of the indicator function.
    # The saved old source is not available in this checkout; do not claim it is.
    raw_prepare = FunctionType(scoring.prepare.__code__,
        {**scoring.prepare.__globals__, 'normalise_candles': lambda frame: frame},
        name='raw_candle_indicator_replay', argdefs=scoring.prepare.__defaults__)
    saved = {(item['symbol'], item['side']): item for item in report['reviewed']}
    rows, coverage, arithmetic, failures = [], [], [], []
    with sqlite3.connect('file:'+str(cache_path.resolve())+'?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        inventory = dict(db.execute('SELECT kind,COUNT(*) FROM records GROUP BY kind').fetchall())
        universe = json.loads(db.execute("SELECT payload FROM records WHERE kind='universe' AND identity='current'").fetchone()[0])
        contracts = {item['tradingsymbol']: item for item in universe['instruments']
                     if item.get('segment') == 'NFO-FUT'}
        names = [row[0] for row in db.execute(
            "SELECT identity FROM records WHERE kind='candles' AND identity LIKE 'NFO:%:5minute' ORDER BY identity")]
        for identity in names:
            contract = identity.split(':')[1]
            raw = daily_raw = None
            symbol = contracts.get(contract, {}).get('name')
            if not symbol:
                match = next((item for item in report['reviewed']
                              if (item.get('contract') or {}).get('tradingsymbol') == contract), None)
                symbol = match['symbol'] if match else contract
            try:
                raw = read_frame(db, identity)
                raw = raw.loc[raw.index+pd.Timedelta(minutes=5) <= cutoff]
                clean = normalise_candles(raw, '5minute', cutoff)
                diagnostics = clean.attrs['session_normalization']
                session_dates = sorted(set(clean.index.date))
                missing_by_session = {}
                for day in session_dates:
                    opening = pd.Timestamp(day, tz='Asia/Kolkata')+pd.Timedelta(hours=9, minutes=15)
                    expected = pd.date_range(opening, periods=75, freq='5min')
                    missing = expected.difference(clean.index)
                    if len(missing):
                        missing_by_session[day.isoformat()] = [stamp.isoformat() for stamp in missing]
                coverage.append({'symbol': symbol, 'contract': contract,
                    'first_candle': raw.index[0].isoformat(), 'last_candle': raw.index[-1].isoformat(),
                    'valid_sessions': len(session_dates), 'missing_by_session': missing_by_session,
                    **diagnostics})
                daily_raw = read_frame(db, 'NFO:'+contract+':day')
                daily_raw = daily_raw.loc[daily_raw.index.date < now.date()]
                daily_clean = normalise_candles(daily_raw, 'day', now)
                raw_atr = float(raw_prepare(daily_raw).ATR.iloc[-1])
                clean_atr = float(scoring.prepare(daily_clean).ATR.iloc[-1])
                raw_indicators, clean_indicators = raw_prepare(raw), scoring.prepare(clean)
                for side in ('LONG', 'SHORT'):
                    before = scoring.directional_setup(raw_indicators, side, config=config,
                                                       daily_atr=raw_atr, prepared=True)
                    after = scoring.directional_setup(clean_indicators, side, config=config,
                                                      daily_atr=clean_atr, prepared=True)
                    original = saved.get((symbol, side), {})
                    original_setup = original.get('futures_setup') or {}
                    names_to_compare = ('rsi_14', 'adx_14', 'macd_histogram', 'relative_volume',
                                        'vwap', 'support', 'resistance', 'target_space_percent', 'daily_atr')
                    before_evidence = {key: before['evidence'].get(key) for key in names_to_compare}
                    after_evidence = {key: after['evidence'].get(key) for key in names_to_compare}
                    bscore, ascore = before['technical_score'], after['technical_score']
                    rows.append({'symbol': symbol, 'contract': contract, 'side': side,
                        'last_candle': clean.index[-1].isoformat(),
                        'saved_report_technical_score': original.get('technical_score'),
                        'saved_report_futures_score': original_setup.get('technical_score'),
                        'saved_report_final_decision': original.get('final_decision'),
                        'saved_report_rejection_reasons': original.get('reason_codes', []),
                        'raw_replay_score': bscore, 'normalized_replay_score': ascore,
                        'score_delta': round(ascore-bscore, 2) if ascore is not None and bscore is not None else None,
                        'raw_timing': before['timing'], 'normalized_timing': after['timing'],
                        'raw_confirmed': before.get('confirmed'), 'normalized_confirmed': after.get('confirmed'),
                        'raw_setup': before['setup_type'], 'normalized_setup': after['setup_type'],
                        'raw_reasons': before['reason_codes'], 'normalized_reasons': after['reason_codes'],
                        'raw_evidence': before_evidence, 'normalized_evidence': after_evidence,
                        'indicators_changed': before_evidence != after_evidence,
                        'original_futures_score_matches_raw_replay': original_setup.get('technical_score') == bscore
                            if original_setup.get('technical_score') is not None else None,
                        'corrected_approval': 'NOT_REEVALUATED_RESEARCH_ONLY'})
                if symbol in {'ICICIBANK', 'UNIONBANK', 'GODREJCP'}:
                    spot_raw = read_frame(db, 'NSE:'+symbol+':5minute')
                    spot = normalise_candles(spot_raw, '5minute', cutoff)
                    common = clean.index.intersection(spot.index)
                    if common.empty:
                        raise ValueError('No same-timestamp equity/Futures candle reference for arithmetic')
                    stamp = common[-1]
                    future_price, spot_price = float(clean.loc[stamp].Close), float(spot.loc[stamp].Close)
                    lot = int(contracts[contract]['lot_size'])
                    for side in ('LONG', 'SHORT'):
                        sign = 1 if side == 'LONG' else -1
                        old_target, old_stop = future_price+sign*spot_price*.003, future_price-sign*spot_price*.002
                        new_target, new_stop = future_price*(1+sign*.003), future_price*(1-sign*.002)
                        profit = costs.round_trip(future_price, new_target, lot, side, stamp.date())
                        loss = costs.round_trip(future_price, new_stop, lot, side, stamp.date())
                        arithmetic.append({'symbol': symbol, 'contract': contract, 'side': side,
                            'reference_kind': 'SAME_TIMESTAMP_COMPLETED_CANDLE_CLOSES_NOT_EXECUTABLE_PRICES',
                            'timestamp': stamp.isoformat(), 'futures_close': future_price,
                            'equity_close': spot_price, 'lot_size': lot,
                            'old_underlying_offset_target': old_target, 'old_underlying_offset_stop': old_stop,
                            'corrected_futures_target_arithmetic': new_target,
                            'corrected_futures_stop_arithmetic': new_stop,
                            'target_difference': new_target-old_target, 'stop_difference': new_stop-old_stop,
                            'corrected_gross_profit': profit['gross_pnl'],
                            'corrected_net_profit': profit['net_pnl'],
                            'corrected_gross_loss': abs(loss['gross_pnl']),
                            'corrected_net_loss': abs(loss['net_pnl']),
                            'corrected_net_rr': profit['net_pnl']/abs(loss['net_pnl']),
                            'minimum_net_rr': config.minimum_net_rr,
                            'target_costs': profit['cost_breakdown'], 'stop_costs': loss['cost_breakdown'],
                            'cost_status': 'PROVISIONAL_EXECUTION_COSTS_NO_CONTRACT_NOTE_OR_SPREAD_EVIDENCE',
                            'legacy_total_cost_comparison': None,
                            'legacy_total_cost_reason': 'Original cost source snapshot unavailable; no invented baseline',
                            'approval': 'NOT_EVALUATED_RESEARCH_ARITHMETIC_ONLY'})
            except (ValueError, TypeError, KeyError, IndexError) as exc:
                failures.append({'symbol': symbol, 'contract': contract, 'reason': str(exc),
                    'daily_source_errors': invalid_candle_details(daily_raw),
                    'five_minute_source_errors': invalid_candle_details(raw)})
    after_hashes = {str(path): digest(path) for path in (report_path, cache_path)}
    source_unchanged = before_hashes == after_hashes
    if not source_unchanged:
        raise RuntimeError('Source files changed during offline comparison; rerun with an immutable snapshot')
    saved_futures = [row for row in rows if row['saved_report_futures_score'] is not None]
    return {'comparison_type': 'OFFLINE_NORMALIZATION_ISOLATION_AND_RESEARCH_PRICE_ARITHMETIC',
        'calculation_version': CALCULATION_VERSION, 'source_report_generated_at': report['generated_at'],
        'completed_session_as_of': cutoff.isoformat(), 'inputs_sha256': before_hashes,
        'source_files_unchanged': source_unchanged,
        'method': 'Identical indicator/scoring formulas and configured weights run on raw retained candles versus canonical normalized candles; saved original report scores are separate evidence. This is not a full original-engine replay.',
        'not_performed': ['Market-data or broker requests', 'Order actions', 'Scanner execution',
                          'Historical backtests or probability preparation', 'Approval reevaluation'],
        'cache_record_counts': inventory,
        'summary': {'futures_contract_histories': len(coverage), 'directional_replays': len(rows),
            'excluded_rows': sum(row['excluded_rows'] for row in coverage),
            'changed_rounded_technical_scores': sum(row['score_delta'] not in (None, 0) for row in rows),
            'changed_indicator_evidence': sum(row['indicators_changed'] for row in rows),
            'changed_timing': sum(row['raw_timing'] != row['normalized_timing'] for row in rows),
            'saved_futures_score_records_compared': len(saved_futures),
            'saved_scores_matching_raw_replay': sum(row['original_futures_score_matches_raw_replay'] for row in saved_futures),
            'saved_approved_count': report['approved_count'], 'corrected_approved_count': None,
            'corrected_approval_status': 'NOT_REEVALUATED_NO_FRESH_EXECUTION_OR_RECONCILIATION',
            'historical_probability': None, 'historical_probability_status': 'UNKNOWN_NOT_PREPARED'},
        'saved_report_rankings': {key: [{'symbol': row['symbol'], 'side': row['side'],
            'technical_score': row['technical_score'], 'decision': row['final_decision']} for row in report[key]]
            for key in ('report_a', 'report_b', 'report_c')},
        'replayed_score_comparisons': rows, 'normalization_coverage': coverage,
        'price_basis_and_current_cost_arithmetic': arithmetic, 'failures': failures}


def render(result):
    summary = result['summary']
    lines = ['# Futures correction comparison', '',
        'Offline research comparison from the saved execution at '+result['source_report_generated_at']+'.', '',
        result['method'], '',
        '**No scan, broker request, order action, historical backtest, or probability preparation occurred.** '
        'Existing Reports A–E and source SQLite were not modified.', '',
        f"Compared {summary['futures_contract_histories']} cached Futures contracts in {summary['directional_replays']} directional replays. "
        f"Excluded {summary['excluded_rows']} invalid session rows. "
        f"Rounded scores changed in {summary['changed_rounded_technical_scores']} records; "
        f"indicator evidence changed in {summary['changed_indicator_evidence']}; timing changed in {summary['changed_timing']}.", '',
        f"Of {summary['saved_futures_score_records_compared']} retained original Futures scores, "
        f"{summary['saved_scores_matching_raw_replay']} matched the raw-row replay. Differences, if any, are retained in JSON rather than called an exact historical reproduction.", '',
        'Saved approved trades: '+str(summary['saved_approved_count'])+'. Corrected approvals: **not reevaluated**. '
        'Fresh depth, news, all mandatory gates and broker reconciliation were not rerun. No approval equivalence is asserted.', '',
        '## Directional score replay', '',
        '| Stock | Side | Saved Futures score | Raw replay | Normalized replay | Change | Timing before → after |',
        '|---|---|---:|---:|---:|---:|---|']
    for row in sorted(result['replayed_score_comparisons'], key=lambda r: (r['symbol'], r['side'])):
        val = lambda key: 'UNKNOWN' if row[key] is None else str(row[key])
        lines.append(f"| {row['symbol']} | {row['side']} | {val('saved_report_futures_score')} | {val('raw_replay_score')} | {val('normalized_replay_score')} | {val('score_delta')} | {row['raw_timing']} → {row['normalized_timing']} |")
    lines += ['', '## Price-basis arithmetic using observed candle closes', '',
        '**These are same-timestamp research reference prices, not executable quotes or actual fills.** '
        'The equity series lacks the last three session candles; the comparison therefore uses the latest timestamp shared by both series. '
        'Levels are unrounded arithmetic, not proposed orders.', '',
        '| Stock | Side | Timestamp | Futures / equity close | Previous target / stop | Futures target / stop | Current modeled net RR |',
        '|---|---|---|---|---|---|---:|']
    for row in result['price_basis_and_current_cost_arithmetic']:
        lines.append(f"| {row['symbol']} | {row['side']} | {row['timestamp']} | {row['futures_close']:.4f} / {row['equity_close']:.4f} | {row['old_underlying_offset_target']:.5f} / {row['old_underlying_offset_stop']:.5f} | {row['corrected_futures_target_arithmetic']:.5f} / {row['corrected_futures_stop_arithmetic']:.5f} | {row['corrected_net_rr']:.4f} |")
    lines += ['', 'Net RR uses current fee rules and provisional configured slippage. No observed spread or contract-note calibration was available. '
        'Minimum net RR remains 1.0. Prior total costs are unavailable because the original source snapshot was lost; they are not invented.', '',
        '## Preserved saved rankings', '']
    for key, rows in result['saved_report_rankings'].items():
        lines.append(key.upper()+': '+(', '.join(f"{row['symbol']} {row['technical_score']} ({row['decision']})" for row in rows) or 'No approved trades')+'.')
    lines += ['', 'Detailed rows, exclusion timestamps, missing candles, reason changes, hashes and cost components are in the companion JSON.', '',
        'Historical probabilities remain UNKNOWN. These calculations do not produce new trade recommendations.', '']
    if result['failures']:
        lines += ['Comparison failures: '+json.dumps(result['failures']), '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT/'reports/report_e_execution.json')
    parser.add_argument('--cache', type=Path, default=ROOT/'.cache/futures_prepared/prepared.sqlite3')
    parser.add_argument('--output-prefix', type=Path, default=ROOT/'reports/futures_correction_comparison')
    args = parser.parse_args()
    result = collect(args.report.resolve(), args.cache.resolve())
    args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
    args.output_prefix.with_suffix('.json').write_text(json.dumps(result, indent=2, allow_nan=False))
    args.output_prefix.with_suffix('.md').write_text(render(result))
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
