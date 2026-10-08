"""Reuse company research unchanged; directional interpretation cannot waive gates."""
from dataclasses import asdict
import json
from pathlib import Path

from src.quality.engine import CandidateQualityEngine, _returns
from src.quality.futures_selection import REQUIRED_CHECKS
from src.quality.models import QualityScore

SHORT_REVIEW_RULES = {
    'valuation_quality': 'The existing rule requires PE near sector PE; overvaluation may support a SHORT thesis but still fails this rule.',
    'debt_free_quality': 'The existing rule requires zero borrowings; debt risk is context for SHORT, not permission to waive the rule.',
    'roe_quality': 'The existing minimum ROE rule conflicts with selecting deteriorating profitability for a SHORT thesis.',
    'roce_quality': 'The existing minimum ROCE rule is retained; weak ROCE is context, not a waiver.',
    'institutional_holding_quality': 'The existing rule requires stable/increasing holdings; institutional selling may support SHORT but remains FAIL.',
    'promoter_holding_quality': 'The existing promoter holding/pledge thresholds remain mandatory despite bearish risk evidence.',
    'quarterly_results_quality': 'The existing rule requires positive growth; negative earnings trends may support SHORT but still fail it.',
    'commentary_quality': 'The existing rule requires VERY_STRONG commentary; bearish commentary is not an automatic exemption.',
    'recent_news_quality': 'The existing rule rejects negative news even if it supports SHORT; event safety remains independently mandatory.',
    'sector_one_year_quality': 'The existing rule requires positive sector return; a weak sector may support SHORT but remains FAIL.',
    'sector_leadership_quality': 'The existing rule requires stock outperformance; underperformance is not silently inverted into PASS.',
    'vwap_quality': 'The existing underlying-stock rule requires price at/above VWAP; below-VWAP SHORT analysis needs explicit policy review.',
}


class CompanyResearch:
    def __init__(self, config, provider):
        self.engine = CandidateQualityEngine(config, fundamental_provider=provider)
        try:
            self.company_types = json.loads(Path('resources/event_risk_config.json').read_text()).get('company_types', {})
        except (OSError, ValueError):
            self.company_types = {}

    def assess(self, symbol, sector, side, *, news, stock_history=None, sector_history=None, session_vwap=None):
        annual = lambda frame: (_returns(frame, (252,)) or {}).get(252)
        scores = self.engine.stock_selection_quality(symbol, sector_one_year_return=annual(sector_history),
            stock_one_year_return=annual(stock_history), news=news)
        scores['valuation_quality'] = self.engine.valuation_quality(symbol)
        scores['delivery_quality'] = self.engine.delivery_quality(symbol)
        scores['vwap_quality'] = session_vwap or QualityScore(None, 'UNKNOWN', 0,
            reason_codes=['UNDERLYING_SESSION_VWAP_UNVERIFIED'])
        snapshot = self.engine._fundamental_snapshot(symbol)
        fundamental = self.engine.fundamental_quality(symbol).to_dict()
        checks = {key: scores[key].to_dict() for key in REQUIRED_CHECKS}
        missing = [key for key, check in checks.items() if check['status'] == 'UNKNOWN']
        failed = [key for key, check in checks.items() if check['status'] == 'FAIL']
        flags = [{'check': key, 'status': checks[key]['status'], 'reason': reason}
                 for key, reason in SHORT_REVIEW_RULES.items()
                 if side == 'SHORT' and checks[key]['status'] == 'FAIL']
        lender = (sector in {'BANKING', 'PSU_BANK', 'NBFC', 'HOUSING_FINANCE'}
                  or self.company_types.get(symbol) == 'NBFC' or symbol in {'PNBHOUSING', 'LICHSGFIN'})
        sector_note = ('Lending-company context: borrowings finance lending and ROCE/debt-to-equity are not directly comparable '
                       'with industrial companies. Existing thresholds are unchanged. Asset quality, credit cost, NIM and capital '
                       'adequacy are not supplied by this pipeline; do not infer them.' if lender else
                       'Financial-services subtype unverified: do not assume a bank/NBFC balance sheet or infer asset quality.'
                       if sector == 'FINANCIAL_SERVICES' else
                       'Non-lending-company context: compare leverage and profitability with dated, like-for-like sector data.')
        if lender:
            flags += [{'check': key, 'status': checks[key]['status'],
                       'reason': 'Sector interpretation requires review for lending companies; no automatic exemption.'}
                      for key in ('debt_free_quality', 'roce_quality') if checks[key]['status'] != 'PASS']
        interpretations = self._interpret(snapshot, checks, news, lender)
        return {'status': 'UNKNOWN' if missing else 'FAIL' if failed else 'PASS',
                'eligible': not missing and not failed, 'checks': checks, 'failed_checks': failed,
                'unavailable_checks': missing, 'policy_review_required': bool(flags), 'review_flags': flags,
                'fundamental_assessment': fundamental, 'fundamentals': asdict(snapshot) if snapshot else None,
                'short_interpretation': interpretations, 'sector_interpretation': sector_note,
                'thresholds': {'maximum_sector_pe_deviation': self.engine.config.maximum_sector_pe_deviation,
                    'minimum_roe_percent': self.engine.config.minimum_roe_percent,
                    'minimum_roce_percent': self.engine.config.minimum_roce_percent,
                    'minimum_fii_holding_percent': self.engine.config.minimum_fii_holding_percent,
                    'minimum_dii_holding_percent': self.engine.config.minimum_dii_holding_percent,
                    'minimum_promoter_holding_percent': self.engine.config.minimum_promoter_holding_percent},
                'policy': 'Original research thresholds/statuses retained for both sides. Directional interpretation never grants execution approval or waives a failed rule.'}

    @staticmethod
    def _interpret(snapshot, checks, news, lender):
        rows = []
        def add(key, stance, reason, values):
            rows.append({'condition': key, 'short_thesis': stance, 'reason': reason, 'values': values})
        if snapshot is None:
            return [{'condition': key, 'short_thesis': 'UNKNOWN', 'values': check.get('factors', {}),
                     'reason': 'Company data unavailable; no bearish inference.'} for key, check in checks.items()]
        s = snapshot
        pe = checks['valuation_quality']
        add('valuation', 'SUPPORTS_SHORT' if 'PE_ABOVE_SECTOR_RANGE' in pe['reason_codes'] else
            'CONTRADICTS_SHORT' if pe['status'] != 'UNKNOWN' else 'UNKNOWN',
            'Relative overvaluation can support a SHORT thesis; valuation alone is not an entry signal.', pe['factors'])
        rising = None if s.previous_total_debt is None or s.total_debt is None else s.total_debt > s.previous_total_debt
        add('debt_trend', 'SECTOR_REVIEW' if lender else 'UNKNOWN' if rising is None else
            'SUPPORTS_SHORT' if rising else 'CONTRADICTS_SHORT',
            'Compare actual reported borrowings across periods; high debt alone does not establish rising debt or distress.',
            {'total_debt': s.total_debt, 'previous_total_debt': s.previous_total_debt,
             'debt_to_equity': s.debt_to_equity, 'previous_debt_to_equity': s.previous_debt_to_equity})
        for name, current, previous, key in (('roe', s.roe, s.previous_roe, 'roe_quality'), ('roce', s.roce, s.previous_roce, 'roce_quality')):
            stance = 'UNKNOWN' if current is None else 'SUPPORTS_SHORT' if checks[key]['status'] == 'FAIL' or (previous is not None and current < previous) else 'CONTRADICTS_SHORT'
            add(name, 'SECTOR_REVIEW' if lender and name == 'roce' else stance,
                'Below-threshold profitability is risk context; a decline is established only when prior comparable data exists.',
                {'current': current, 'previous': previous, **checks[key]['factors']})
        for prefix in ('fii', 'dii'):
            change = getattr(s, prefix+'_holding_change_pct_points')
            add(prefix, 'UNKNOWN' if change is None else 'SUPPORTS_SHORT' if change < 0 else 'CONTRADICTS_SHORT',
                'Negative quarter-on-quarter change indicates reported institutional reduction, not proof of current intraday selling.',
                {'holding_percent': getattr(s, prefix+'_holding_percent'), 'change_percentage_points': change})
        add('promoter', 'UNKNOWN' if checks['promoter_holding_quality']['status'] == 'UNKNOWN' else
            'SUPPORTS_SHORT' if (s.promoter_holding_change_pct_points or 0) < 0 or (s.promoter_pledge or 0) > 0 else 'NEUTRAL',
            'Declining promoter holdings or pledge are risk context; low holdings alone do not establish active selling.',
            checks['promoter_holding_quality']['factors'])
        add('delivery', 'UNKNOWN' if checks['delivery_quality']['status'] == 'UNKNOWN' else 'NEUTRAL',
            'Delivery percentage has no directional sign. Interpret it alongside price and directional volume; do not label delivery as short selling.',
            checks['delivery_quality']['factors'])
        for name, values in (('revenue_growth', s.quarterly_revenue_growth_pct), ('profit_growth', s.quarterly_profit_growth_pct)):
            known = values is not None and len(values) >= 3
            negative = known and any(value <= 0 for value in values[-3:])
            slowing = known and values[-1] < values[-2] < values[-3]
            add(name, 'UNKNOWN' if not known else 'SUPPORTS_SHORT' if negative else 'MIXED' if slowing else 'CONTRADICTS_SHORT',
                'Non-positive YoY growth supports earnings-risk context. Slowing positive growth is not a reported decline in absolute earnings.',
                {'last_three_quarters_yoy_percent': values, 'slowing_positive_growth': slowing and not negative})
        add('news', 'UNKNOWN' if checks['recent_news_quality']['status'] == 'UNKNOWN' else
            'SUPPORTS_SHORT' if checks['recent_news_quality']['status'] == 'FAIL' else 'CONTRADICTS_SHORT' if news.get('sentiment') == 'BULLISH' else 'NEUTRAL',
            'Model/news interpretation is separate from confirmed material-event risk and cannot grant entry approval.',
            {'news_state': news.get('news_state'), 'sentiment': news.get('sentiment'), 'checked_at': news.get('checked_at')})
        commentary = s.evidence.get('commentary_strength', {})
        add('commentary', 'UNKNOWN' if s.commentary_strength is None else
            'SUPPORTS_SHORT' if commentary.get('negative_signal_count', 0) > 0 else
            'CONTRADICTS_SHORT' if s.commentary_strength == 'VERY_STRONG' else 'NEUTRAL',
            'Prepared-remarks interpretation is not a company rating; a rule failure alone is not proof of negative guidance.',
            {'strength': s.commentary_strength, 'evidence': commentary})
        for condition, key in (('sector_one_year', 'sector_one_year_quality'), ('stock_vs_sector', 'sector_leadership_quality'),
                               ('underlying_vwap', 'vwap_quality')):
            value = checks[key]
            add(condition, 'UNKNOWN' if value['status'] == 'UNKNOWN' else 'SUPPORTS_SHORT' if value['status'] == 'FAIL' else 'CONTRADICTS_SHORT',
                'Directional context only. The original bullish-oriented rule status remains unchanged and a failure requires policy review.',
                value['factors'])
        add('block_deal', 'UNKNOWN' if s.block_deal_price_impact is None else 'REVIEW' if s.block_deal_price_impact else 'NEUTRAL',
            'A block-deal flag does not establish trade direction or causal price impact.', {'block_deal_price_impact': s.block_deal_price_impact})
        return rows
