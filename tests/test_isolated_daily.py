import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.futures_workspace import isolated_daily


def test_ui_worker_uses_cli_and_saved_pair_without_mutating_parent(tmp_path,monkeypatch):
    monkeypatch.setenv('KITE_ACCESS_TOKEN','stale-parent')
    monkeypatch.setattr(isolated_daily,'dotenv_values',lambda *args,**kwargs:{'KITE_API_KEY':'saved-key','KITE_ACCESS_TOKEN':'saved-token'})
    def run(command,**kwargs):
        assert command[2]=='daily'
        assert kwargs['env']['KITE_ACCESS_TOKEN']=='saved-token'
        assert kwargs['env']['MARKET_DATA_SOURCE']=='kite'
        assert Path(command[command.index('--database')+1]).is_absolute()
        result=command[command.index('--result-file')+1]
        Path(result).write_text(json.dumps({'status':'COMPLETED','selected_count':30}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(isolated_daily.subprocess,'run',run)
    assert isolated_daily.run_daily(tmp_path/'ui.db')['selected_count']==30
    assert os.environ['KITE_ACCESS_TOKEN']=='stale-parent'


def test_failed_child_does_not_return_previous_saved_result(tmp_path,monkeypatch):
    monkeypatch.setattr(isolated_daily,'dotenv_values',lambda *args,**kwargs:{})
    monkeypatch.setattr(isolated_daily.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError,match='isolated daily scan failed'):
        isolated_daily.run_daily(tmp_path/'ui.db')
