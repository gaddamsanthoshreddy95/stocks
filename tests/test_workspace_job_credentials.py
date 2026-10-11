from types import SimpleNamespace
from unittest.mock import Mock,patch
from src.futures_workspace.ui import create_job_workspace


def test_each_manual_job_gets_fresh_platform_and_reloads_saved_credentials():
    platforms=[object(),object()]
    workspaces=[SimpleNamespace(adapter=Mock()),SimpleNamespace(adapter=Mock())]
    settings=SimpleNamespace(market_data_source='kite')
    with patch('src.futures_workspace.ui.TradingPlatform',side_effect=platforms),patch('src.futures_workspace.ui.WorkspaceStore'),patch('src.futures_workspace.ui.FuturesWorkspace',side_effect=workspaces) as factory:
        assert create_job_workspace(settings,'example.db') is workspaces[0]
        assert create_job_workspace(settings,'example.db') is workspaces[1]
        assert [call.args[0] for call in factory.call_args_list]==platforms
        for workspace in workspaces:workspace.adapter.refresh_credentials.assert_called_once_with()


def test_offline_job_does_not_reload_broker_credentials():
    workspace=SimpleNamespace(adapter=Mock())
    with patch('src.futures_workspace.ui.TradingPlatform'),patch('src.futures_workspace.ui.WorkspaceStore'),patch('src.futures_workspace.ui.FuturesWorkspace',return_value=workspace):
        create_job_workspace(SimpleNamespace(market_data_source='cache'),'example.db')
        workspace.adapter.refresh_credentials.assert_not_called()
