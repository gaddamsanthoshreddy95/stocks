"""Separate, uncalibrated Report E hypotheses; never trading-engine configuration."""
from dataclasses import dataclass, field, asdict
from math import isfinite
import json
import os

GROUPS=('technical','volume','fundamentals','futures','context')


@dataclass(frozen=True)
class ReportEConfig:
    long_weights: dict = field(default_factory=lambda:dict(zip(GROUPS,(.35,.15,.25,.15,.10))))
    short_weights: dict = field(default_factory=lambda:dict(zip(GROUPS,(.40,.15,.20,.15,.10))))
    quality_rank_weight: float = .6
    strong_quality_threshold: float = 70
    strong_coverage_percent: float = 80
    historical_spread_bps: float | None = None
    prepare_history: bool = True

    def __post_init__(self):
        for weights in (self.long_weights,self.short_weights):
            if set(weights)!=set(GROUPS) or any(not isinstance(v,(int,float)) or not isfinite(v) or v<0 for v in weights.values()) or abs(sum(weights.values())-1)>1e-9:
                raise ValueError('Report E quality weights must contain all five groups and sum to one')
        for name in ('quality_rank_weight','strong_quality_threshold','strong_coverage_percent'):
            value=getattr(self,name)
            if not isfinite(value) or not 0<=value<=(1 if name=='quality_rank_weight' else 100):
                raise ValueError('Invalid Report E '+name)
        if self.historical_spread_bps is not None and (not isfinite(self.historical_spread_bps) or self.historical_spread_bps<0):
            raise ValueError('Historical spread assumption must be non-negative or unavailable')

    @classmethod
    def from_env(cls):
        base=cls()
        values=asdict(base)
        for name in ('long_weights','short_weights'):
            raw=os.getenv('FUTURES_REPORT_E_'+name.upper())
            if raw is not None: values[name]=json.loads(raw)
        for name in ('quality_rank_weight','strong_quality_threshold','strong_coverage_percent','historical_spread_bps'):
            raw=os.getenv('FUTURES_REPORT_E_'+name.upper())
            if raw is not None: values[name]=float(raw)
        values['prepare_history']=os.getenv('FUTURES_REPORT_E_PREPARE_HISTORY','true').lower() in ('true','1','yes')
        return cls(**values)
