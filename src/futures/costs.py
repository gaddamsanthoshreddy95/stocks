"""Estimated round-trip NSE futures costs; never a broker margin/order request."""
from dataclasses import dataclass, fields
from datetime import date, datetime
from functools import lru_cache
from math import isfinite, floor
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os


@lru_cache(maxsize=1)
def fee_schedule():
    """Local, sourced tariff. Loading this function never contacts a broker."""
    return json.loads((Path(__file__).resolve().parents[2] / 'resources' /
                       'futures_fee_schedule.json').read_text())


@dataclass(frozen=True)
class FuturesCosts:
    brokerage_rate: float = .0003
    brokerage_cap: float = 20
    exchange_rate: float = .0000183  # Inclusive exchange + IPFT rate; split below, never charged twice.
    sebi_rate: float = .000001
    gst_rate: float = .18
    stamp_rate: float = .00002
    slippage_bps: float = 2
    ipft_rate: float = .000000001

    def __post_init__(self):
        if any(not isfinite(getattr(self, f.name)) or getattr(self, f.name) < 0 for f in fields(self)):
            raise ValueError('Cost assumptions must be finite and non-negative')

    @classmethod
    def from_env(cls):
        defaults = cls()
        return cls(**{f.name: float(os.getenv('FUTURES_COST_' + f.name.upper(), getattr(defaults, f.name)))
                      for f in fields(cls)})

    def effective_rates(self, traded):
        schedule = fee_schedule()
        period = next((item for item in schedule['effective_periods']
                       if item['from'] <= traded.isoformat() and
                       (not item['until_exclusive'] or traded.isoformat() < item['until_exclusive'])), None)
        if period is None:
            raise ValueError('Cost schedule supports trades from 2024-10-01 onward')
        # Retain the legacy numeric configuration fields, explicitly interpreting
        # the published 0.00183% as inclusive of IPFT. Defaults follow the dated
        # split. Custom assumptions remain visible and are not called verified.
        defaults = type(self)()
        ipft = period['ipft_rate'] if self.ipft_rate == defaults.ipft_rate else self.ipft_rate
        if ipft > self.exchange_rate:
            raise ValueError('Inclusive exchange rate cannot be smaller than IPFT allocation')
        return {'stt_rate': period['stt_sell_rate'], 'exchange_rate': self.exchange_rate-ipft,
                'ipft_rate': ipft, 'exchange_including_ipft_rate': self.exchange_rate,
                'effective_from': period['from'], 'schedule_version': schedule['version'],
                'tariff_status': 'OFFICIAL_STANDARD_TARIFF' if all(
                    getattr(self, f.name) == getattr(defaults, f.name)
                    for f in fields(self) if f.name != 'slippage_bps') else 'CUSTOM_ASSUMPTIONS',
                'verified_on_ist': schedule['verified_on_ist']}

    def round_trip(self, entry, exit_price, quantity, side, trade_date=None, extra_slippage_bps=0,
                   *, exit_spread_bps=0):
        if side not in {'LONG', 'SHORT'} or quantity <= 0 or int(quantity) != quantity:
            raise ValueError('Valid direction and positive integer quantity required')
        if not all(isfinite(v) and v > 0 for v in (entry, exit_price)):
            raise ValueError('Positive finite execution prices required')
        if any(not isfinite(value) or value < 0 for value in (extra_slippage_bps, exit_spread_bps)):
            raise ValueError('Slippage and spread assumptions must be finite and non-negative')
        if extra_slippage_bps and exit_spread_bps:
            raise ValueError('Use either legacy extra slippage or explicit exit spread, not both')
        traded = date.fromisoformat(str(trade_date)[:10]) if trade_date else datetime.now(ZoneInfo('Asia/Kolkata')).date()
        buy, sell = (entry, exit_price) if side == 'LONG' else (exit_price, entry)
        buy_turnover, sell_turnover = buy * quantity, sell * quantity
        turnover = buy_turnover + sell_turnover
        brokerage = min(buy_turnover * self.brokerage_rate, self.brokerage_cap) + min(
            sell_turnover * self.brokerage_rate, self.brokerage_cap)
        rates = self.effective_rates(traded)
        stt_rate = rates['stt_rate']
        exchange, sebi, ipft = turnover * rates['exchange_rate'], turnover * self.sebi_rate, turnover * rates['ipft_rate']
        gst = (brokerage + exchange + sebi + ipft) * self.gst_rate
        slippage = turnover * (self.slippage_bps + extra_slippage_bps) / 10000
        components = {'brokerage': brokerage, 'stt': sell_turnover * stt_rate, 'gst': gst,
                      'exchange_fees': exchange, 'sebi_fees': sebi, 'stamp_duty': buy_turnover * self.stamp_rate,
                      'ipft': ipft, 'slippage': slippage}
        if exit_spread_bps:
            # Entry bid/ask and depth are already embedded in the supplied entry.
            components['spread'] = exit_price * quantity * exit_spread_bps / 20000
        gross = (exit_price - entry) * quantity * (1 if side == 'LONG' else -1)
        return {'gross_pnl': gross, 'net_pnl': gross - sum(components.values()),
                'total_costs': sum(components.values()), 'cost_breakdown': components,
                'stt_rate': stt_rate, 'cost_basis': 'Sourced NSE/Zerodha tariff estimate; not a contract note',
                'fee_schedule': rates, 'execution_cost_calibration': 'PROVISIONAL',
                'slippage_basis': 'Residual execution/latency allowance beyond quoted spread and displayed-depth impact; not empirically calibrated',
                'brokerage_order_assumption': 'One entry order and one exit order; partial fills aggregate within each order',
                'spread_basis': 'Entry embedded in execution price; estimated half-spread on exit only' if exit_spread_bps else 'No explicit spread supplied',
                'rounding_basis': 'Unrounded per-trade allocations; STT rounds half up after client/day aggregation',
                'account_tariff_status': 'UNVERIFIED_STANDARD_RESIDENT_TARIFF'}


def trade_plan(entry, lot_size, side, *, config, costs, risk_budget, trade_date, margin_per_lot=None,
               available_capital=None, extra_slippage_bps=0, movement_reference_price=None,
               exit_spread_bps=0):
    if side not in {'LONG', 'SHORT'} or not isfinite(entry) or entry <= 0:
        raise ValueError('Valid futures entry and direction required')
    if not isfinite(lot_size) or lot_size <= 0 or int(lot_size) != lot_size:
        raise ValueError('Valid contract lot size required')
    sign = 1 if side == 'LONG' else -1
    # Equity context cannot affect executable Futures prices, including when invalid.
    try:
        reference = float(movement_reference_price)
        if not isfinite(reference) or reference <= 0:
            reference = None
    except (ValueError, TypeError):
        reference = None
    target, stop = entry + sign*entry*config.target_fraction, entry - sign*entry*config.stop_fraction
    def economics(exit_price, quantity):
        return costs.round_trip(entry, exit_price, quantity, side, trade_date, extra_slippage_bps,
                                exit_spread_bps=exit_spread_bps)
    one_loss = economics(stop, lot_size)
    lots = min(config.maximum_lots, max(0, floor(risk_budget / abs(one_loss['net_pnl']))))
    if margin_per_lot is not None:
        if not isfinite(margin_per_lot) or margin_per_lot <= 0:
            raise ValueError('Available margin data must be positive and finite')
        if available_capital is not None:
            lots = min(lots, floor(available_capital / margin_per_lot))
    quantity = lots * lot_size
    # Keep per-lot economics visible when the configured budget cannot fit a lot.
    profit = economics(target, quantity or lot_size)
    loss = economics(stop, quantity or lot_size)
    return {'entry': entry, 'target': target, 'stop_loss': stop, 'lot_size': lot_size,
            'movement_basis': 'FUTURES',
            'entry_price_kind': 'MODELED_FUTURES_EXECUTION_REFERENCE_NOT_BROKER_FILL',
            'underlying_entry': reference,
            'underlying_target': None,
            'underlying_stop_loss': None,
            'underlying_context': {'reference_price': reference, 'purpose': 'RESEARCH_ONLY_NOT_EXECUTION_LEVELS'},
            'basis_assumption': 'Fixed percentages of Futures entry; equity reference is research only',
            'number_of_lots': lots, 'quantity': quantity, 'economics_quantity': quantity or lot_size,
            'gross_profit_at_target': profit['gross_pnl'], 'gross_loss_at_stop': abs(loss['gross_pnl']),
            'net_profit_at_target': profit['net_pnl'], 'net_loss_at_stop': abs(loss['net_pnl']),
            'target_costs': profit['cost_breakdown'], 'stop_costs': loss['cost_breakdown'],
            'cost_evidence': {key: profit[key] for key in ('fee_schedule', 'execution_cost_calibration',
                'brokerage_order_assumption', 'spread_basis', 'slippage_basis', 'rounding_basis', 'account_tariff_status')},
            'net_risk_reward': profit['net_pnl'] / abs(loss['net_pnl']),
            'margin_per_lot': margin_per_lot,
            'margin_requirement': lots * margin_per_lot if margin_per_lot is not None else None,
            'margin_status': 'AVAILABLE' if margin_per_lot is not None else 'UNKNOWN',
            'sizing_basis': 'Loss budget including costs; affordability unverified when margin unavailable'}
