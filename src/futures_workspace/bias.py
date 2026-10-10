"""Report-only market bias; missing inputs never become neutral observations."""
from math import isfinite

class MarketBias:
    # Each input is normalized -1..1 with its own source/time evidence.
    WEIGHTS={'nifty_vwap':.15,'nifty_trend':.20,'bank_trend':.10,'midcap_trend':.10,
             'breadth':.20,'sector_participation':.15,'opening_range':.05,'relative_volume':.05}
    @classmethod
    def evaluate(cls,inputs):
        valid={}
        for name,weight in cls.WEIGHTS.items():
            evidence=inputs.get(name,{})
            value=evidence.get('value')
            if (evidence.get('status')=='PASS' and evidence.get('source') and evidence.get('as_of')
                    and isinstance(value,(float,int)) and isfinite(value) and -1<=value<=1):
                valid[name]=value
        coverage=sum(cls.WEIGHTS[k] for k in valid)
        # Nifty trend plus broad participation required; a single index is insufficient.
        sufficient='nifty_trend' in valid and ('breadth' in valid or 'sector_participation' in valid) and coverage>=.5
        score=round(sum(valid[k]*cls.WEIGHTS[k] for k in valid)/coverage*100,2) if sufficient else None
        label='UNKNOWN' if score is None else 'STRONG_BULLISH' if score>=60 else 'BULLISH' if score>=20 else 'STRONG_BEARISH' if score<=-60 else 'BEARISH' if score<=-20 else 'NEUTRAL'
        adjustment={'STRONG_BULLISH':10,'BULLISH':5,'NEUTRAL':0,'BEARISH':-5,'STRONG_BEARISH':-10,'UNKNOWN':0}[label]
        return {'classification':label,'score':score,'coverage_percent':coverage*100,'inputs':inputs,
            'mode':'REPORT_ONLY','long_adjustment':adjustment,'short_adjustment':-adjustment,
            'thresholds':{'strong':60,'directional':20},'experimental':True}
