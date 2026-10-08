"""Audit a local fee schedule or normalized local observations; never contacts Kite.

Optional --input JSON format:
{"round_trips": [{"entry": 100, "exit": 100.3, "quantity": 500,
  "side": "LONG", "trade_date": "2026-10-08",
  "observed_charges": {"brokerage": 30, "stt": 25}}],
 "fill_observations": [{"transaction_type": "BUY", "fill_price": 100.02,
  "executable_reference_price": 100, "fill_timestamp": "2026-10-08T10:00:01+05:30",
  "reference_timestamp": "2026-10-08T10:00:00+05:30"}]}

Observed charges must be allocated to this exact round trip from a contract note.
The audit reports differences and observations; it never changes model settings.
Quotes must be contemporaneous executable-side prices (not LTP or midprice).
"""
import argparse
from datetime import datetime
import json
from math import isfinite
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.futures.costs import FuturesCosts, fee_schedule


def audit(payload=None, costs=None):
    payload = payload or {}
    costs = costs or FuturesCosts.from_env()
    comparisons, fills, failures = [], [], []
    for index, item in enumerate(payload.get('round_trips', [])):
        try:
            modeled = costs.round_trip(item['entry'], item['exit'], item['quantity'],
                                       item['side'], item['trade_date'])
            observed = item.get('observed_charges', {})
            if not isinstance(observed, dict):
                raise ValueError('observed_charges must contain named numeric charge amounts')
            differences = {}
            for name, amount in observed.items():
                if name not in modeled['cost_breakdown'] or name in {'slippage', 'spread'}:
                    raise ValueError('Contract-note charges must use modeled statutory or brokerage component names')
                if not isinstance(amount, (int, float)) or not isfinite(amount) or amount < 0:
                    raise ValueError('Observed charges must be finite and non-negative')
                differences[name] = {'observed': amount, 'modeled': modeled['cost_breakdown'][name],
                                     'observed_minus_modeled': amount-modeled['cost_breakdown'][name]}
            comparisons.append({'index': index, 'trade_date': item['trade_date'], 'model': modeled,
                'differences': differences, 'status': 'COMPARISON_ONLY' if differences else 'UNKNOWN',
                'reason': 'Daily rounding/allocation and order count require contract-note verification'})
        except (KeyError, TypeError, ValueError) as exc:
            failures.append({'kind': 'round_trip', 'index': index, 'reason': str(exc)})
    for index, item in enumerate(payload.get('fill_observations', [])):
        try:
            fill, reference = item['fill_price'], item['executable_reference_price']
            if not all(isinstance(value, (int, float)) and isfinite(value) and value > 0
                       for value in (fill, reference)):
                raise ValueError('Fill and executable-side reference must be finite positive observed prices')
            stamp = datetime.fromisoformat(item['fill_timestamp'])
            quote_stamp = datetime.fromisoformat(item['reference_timestamp'])
            if stamp.tzinfo is None or quote_stamp.tzinfo is None:
                raise ValueError('Fill/reference timestamps must include timezone offsets')
            age = (stamp-quote_stamp).total_seconds()
            if not 0 <= age <= 2:
                raise ValueError('Reference must precede fill by at most two seconds')
            if item['transaction_type'] not in {'BUY', 'SELL'}:
                raise ValueError('Transaction type must be BUY or SELL')
            sign = 1 if item['transaction_type'] == 'BUY' else -1
            fills.append({'index': index, 'adverse_slippage_bps': sign*(fill-reference)/reference*10000,
                          'quote_age_seconds': age})
        except (KeyError, TypeError, ValueError) as exc:
            failures.append({'kind': 'fill_observation', 'index': index, 'reason': str(exc)})
    return {'read_only': True, 'fee_schedule': fee_schedule(), 'configured_assumptions': costs.__dict__,
            'contract_note_comparisons': comparisons, 'fill_observations': fills, 'failures': failures,
            'calibration_status': 'OBSERVATIONS_ONLY' if comparisons or fills else 'UNKNOWN',
            'slippage_calibration': {'status': 'OBSERVATIONS_ONLY' if fills else 'UNKNOWN',
                'sample_size': len(fills),
                'median_observed_adverse_bps': statistics.median([row['adverse_slippage_bps'] for row in fills]) if fills else None,
                'active_model_bps_per_leg': costs.slippage_bps,
                'active_model_status': 'PROVISIONAL',
                'reason': 'No automatic calibration; representativeness, quantities and market regimes require review'},
            'rounding': {'stt_method': 'Half up on aggregated daily taxable Futures sales',
                'warning': 'Per-trade estimates retain fractional allocation; do not round each trade independently'},
            'not_performed': ['Network or broker calls', 'Order actions', 'Configuration mutation', 'Historical probability preparation']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', type=Path, help='Optional local normalized observations JSON')
    args = parser.parse_args(argv)
    payload = json.loads(args.input.read_text()) if args.input else None
    print(json.dumps(audit(payload), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
