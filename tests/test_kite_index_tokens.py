import pandas as pd
import pytest
from src.providers.kite_provider import KiteProvider


def test_index_and_equity_lookup_excludes_derivatives_and_other_exchanges(monkeypatch):
    rows=pd.DataFrame([
        [256265,'NIFTY 50','NSE','','INDICES'],
        [260105,'NIFTY BANK','NSE','','INDICES'],
        [123,'INFY','NSE','EQ','NSE'],
        [456,'NIFTYFUT','NFO','FUT','NFO-FUT'],
        [789,'SENSEX','BSE','','INDICES'],
    ],columns=['instrument_token','tradingsymbol','exchange','instrument_type','segment'])
    monkeypatch.setattr(KiteProvider,'_instrument_tokens',None)
    monkeypatch.setattr(pd,'read_csv',lambda *args,**kwargs:rows)
    assert KiteProvider._instrument_token('NIFTY 50')==256265
    assert KiteProvider._instrument_token('NIFTY BANK')==260105
    assert KiteProvider._instrument_token('INFY.NS')==123
    for name in ('NIFTYFUT','SENSEX','UNLISTED'):
        with pytest.raises(ValueError):KiteProvider._instrument_token(name)
