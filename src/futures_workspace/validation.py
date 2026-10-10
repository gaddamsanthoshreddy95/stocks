"""Exact OHLCV rejection predicates. Prices are never repaired or synthesized."""
import math
import numpy as np
import pandas as pd

FIELDS=['Open','High','Low','Close','Volume']

class HistoryValidationError(ValueError):
    def __init__(self,details):
        self.details=details
        super().__init__('HISTORY_INVALID:'+','.join(v['rule'] for v in details['violations']))


def ohlcv_diagnostics(frame):
    details={'rows':len(frame),'violations':[]}
    missing=[field for field in FIELDS if field not in frame.columns]
    if missing or frame.columns.has_duplicates:
        details['violations'].append({'rule':'MISSING_OR_DUPLICATE_OHLCV_COLUMNS','missing':missing})
        return details
    values=frame[FIELDS].apply(pd.to_numeric,errors='coerce')
    numeric=values.to_numpy(dtype=float,na_value=np.nan)
    masks={
        'NON_FINITE_OHLCV':pd.Series(~np.isfinite(numeric).all(axis=1),index=values.index),
        'NON_POSITIVE_PRICE':(values[FIELDS[:4]]<=0).any(axis=1),
        'NEGATIVE_VOLUME':values.Volume<0,
        'HIGH_BELOW_OHLC':values.High<values[['Open','Low','Close']].max(axis=1),
        'LOW_ABOVE_OHLC':values.Low>values[['Open','High','Close']].min(axis=1)}
    for rule,mask in masks.items():
        if not mask.any(): continue
        samples=[]
        for stamp,row in values.loc[mask].head(5).iterrows():
            observed={k:(float(v) if pd.notna(v) and math.isfinite(float(v)) else None) for k,v in row.items()}
            samples.append({'timestamp':str(stamp),'observed':observed})
        details['violations'].append({'rule':rule,'count':int(mask.sum()),'samples':samples})
    return details


def require_valid_ohlcv(frame):
    details=ohlcv_diagnostics(frame)
    if details['violations']:
        raise HistoryValidationError(details)
    return details
