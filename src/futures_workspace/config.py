"""Independent discovery hypotheses and conservative workspace deadlines."""
from dataclasses import dataclass, fields
import math
import os

@dataclass(frozen=True)
class WorkspaceConfig:
    enabled: bool = False
    minimum_score: float = 60
    maximum_per_list: int = 25
    confirmation_sessions: int = 3
    hysteresis: float = 8
    recovery_decline_percent: float = 15
    recovery_drawdown_percent: float = 20
    maximum_history_age_sessions: int = 2
    maximum_membership_age_days: int = 8
    technical_weight: float = .7
    fundamental_weight: float = .2
    news_weight: float = .1
    context_ranking_validated: bool = False
    require_adjusted_history: bool = True
    weekly_history_source: str = "yahoo_adjusted"
    minimum_futures_volume: int = 1000
    maximum_spread_percent: float = .15
    weekly_day: int = 5
    weekly_hour: int = 9
    weekly_minute: int = 0
    entry_cutoff_hour: int = 15
    entry_cutoff_minute: int = 0
    flat_hour: int = 15
    flat_minute: int = 10

    def __post_init__(self):
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v,(int,float)) and not isinstance(v, bool) and (not math.isfinite(v) or v < 0):
                raise ValueError(f'{f.name} must be finite and nonnegative')
        if self.weekly_history_source not in ('yahoo_adjusted','kite'):
            raise ValueError('weekly_history_source must be yahoo_adjusted or kite')
        if abs(self.technical_weight+self.fundamental_weight+self.news_weight-1)>1e-8:
            raise ValueError('Discovery weights must sum to one')
        for name in ('maximum_per_list', 'confirmation_sessions'):
            if getattr(self, name)<1 or int(getattr(self,name))!=getattr(self,name):
                raise ValueError(f'{name} must be a positive integer')
        if self.confirmation_sessions < 2:
            raise ValueError('Recovery requires multiple completed sessions')
        if self.weekly_day>6 or any(getattr(self,n)>23 for n in ('weekly_hour','entry_cutoff_hour','flat_hour')) or any(getattr(self,n)>59 for n in ('weekly_minute','entry_cutoff_minute','flat_minute')):
            raise ValueError('Invalid schedule or cutoff')
        if (self.entry_cutoff_hour,self.entry_cutoff_minute)>=(self.flat_hour,self.flat_minute):
            raise ValueError('Entry cutoff must precede manual flat deadline')
        if (self.flat_hour,self.flat_minute)>(15,10):
            raise ValueError('Workspace manual flat deadline cannot exceed 15:10 IST')
        if self.minimum_score>100 or self.hysteresis>100:
            raise ValueError('Scores must be within 0..100')

    @classmethod
    def from_env(cls):
        defaults=cls()
        values={}
        for f in fields(cls):
            raw=os.getenv('FUTURES_WORKSPACE_'+f.name.upper())
            if raw is not None:
                default=getattr(defaults,f.name)
                if isinstance(default,bool):
                    if raw.lower() not in ('true','false','1','0'):
                        raise ValueError(f'Invalid boolean: {f.name}')
                    values[f.name]=raw.lower() in ('true','1')
                else:
                    values[f.name]=type(default)(raw)
        return cls(**values)
