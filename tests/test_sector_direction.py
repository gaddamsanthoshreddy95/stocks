import pytest

from src.futures.sector_direction import alignment
from src.futures.config import FuturesScanConfig
from test_short_confirmation import inputs, NOW
from test_long_confirmation import long_inputs
from src.futures.long_confirmation import evaluate as evaluate_long
from src.futures.short_confirmation import evaluate as evaluate_short


@pytest.mark.parametrize('side,stock,sector,expected',[
    ('LONG',1,.2,'PASS'),('LONG',1,-.2,'BLOCKED'),('LONG',1,0,'BLOCKED'),
    ('SHORT',-1,-.2,'PASS'),('SHORT',-1,.2,'BLOCKED'),('SHORT',-1,0,'BLOCKED'),
    ('LONG',-.1,.2,'BLOCKED'),('SHORT',.1,-.2,'BLOCKED')])
def test_absolute_direction_is_required(side,stock,sector,expected):
    assert alignment(side,stock,sector)['status']==expected


@pytest.mark.parametrize('side,build,evaluate',[
    ('LONG',long_inputs,evaluate_long),('SHORT',inputs,evaluate_short)])
@pytest.mark.parametrize('sector_return',[0,.2,-.2,None])
def test_real_confirmation_requires_sector_support(side,build,evaluate,sector_return):
    data,oi=build()
    if sector_return is None:
        data['short_sector_intraday']=None
    else:
        frame=data['short_sector_intraday']
        price=float(frame.Open.iloc[0])*(1+sector_return/100)
        frame.iloc[-1,frame.columns.get_loc('Close')]=price
        frame.iloc[-1,frame.columns.get_loc('High')]=max(price,float(frame.Open.iloc[-1]))+.1
        frame.iloc[-1,frame.columns.get_loc('Low')]=min(price,float(frame.Open.iloc[-1]))-.1
    gate=evaluate(data,oi,NOW,FuturesScanConfig())
    if sector_return is None:
        assert gate['status']=='UNKNOWN'
    elif (side=='LONG' and sector_return>0) or (side=='SHORT' and sector_return<0):
        assert gate['status']=='PASS',gate
        assert gate['evidence']['sector_direction']['status']=='PASS'
    else:
        assert gate['status']=='BLOCKED',gate
        assert side+'_SECTOR_NOT_'+('BULLISH' if side=='LONG' else 'BEARISH') in gate['reason_codes']


def test_invalid_sector_return_is_not_neutral():
    with pytest.raises(ValueError,match='SECTOR_DIRECTION_UNAVAILABLE'):
        alignment('LONG',1,float('nan'))
