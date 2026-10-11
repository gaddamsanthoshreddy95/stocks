import numpy as np
from unittest.mock import patch
from src.futures_workspace.discovery import technical
from test_futures_workspace import frame,CFG,NOW


def test_indicator_exception_retains_exact_stage_and_reason():
    data=frame(np.linspace(220,100,280))
    with patch('src.futures_workspace.discovery.prepare',side_effect=ValueError('indicator fixture failure')):
        result=technical(data,CFG,NOW)
    assert result['classification']=='UNKNOWN_DATA'
    quality=result['history_quality']
    assert quality['stage']=='INDICATOR_PREPARATION' and quality['fetched_rows']==280
    assert quality['violations']==[{'rule':'PROCESSING_EXCEPTION','error_type':'ValueError','message':'indicator fixture failure'}]


def test_missing_history_records_quality_failure():
    result=technical(None,CFG,NOW)
    assert result['history_quality']['status']=='FAILED'
    assert result['history_quality']['reason']=='HISTORY_MISSING'
