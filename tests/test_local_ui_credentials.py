from scripts.start_ui import load_local_kite_credentials


def test_auth_recovery_updates_cached_client_from_saved_complete_pair(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from src.futures_workspace.adapter import RepositoryAdapter
    monkeypatch.setenv('KITE_API_KEY','stale-key')
    monkeypatch.setenv('KITE_ACCESS_TOKEN','stale-token')
    client=SimpleNamespace(api_key='cached-key',set_access_token=Mock())
    adapter=RepositoryAdapter(SimpleNamespace(provider=SimpleNamespace(provider=SimpleNamespace(kite=client))))
    path=tmp_path/'.env';path.write_text('KITE_API_KEY=fresh-key\nKITE_ACCESS_TOKEN=fresh-token\n')
    assert adapter.refresh_credentials(path)
    assert client.api_key=='fresh-key'
    client.set_access_token.assert_called_once_with('fresh-token')


def test_saved_pair_overrides_stale_inherited_pair(tmp_path, monkeypatch):
    monkeypatch.setenv('KITE_API_KEY', 'old-key')
    monkeypatch.setenv('KITE_ACCESS_TOKEN', 'old-token')
    path = tmp_path / '.env'
    path.write_text('KITE_API_KEY=new-key\nKITE_ACCESS_TOKEN=new-token\n')
    load_local_kite_credentials(path)
    import os
    assert os.environ['KITE_API_KEY'] == 'new-key'
    assert os.environ['KITE_ACCESS_TOKEN'] == 'new-token'


def test_partial_file_cannot_mix_broker_pairs(tmp_path, monkeypatch):
    monkeypatch.setenv('KITE_API_KEY', 'old-key')
    monkeypatch.setenv('KITE_ACCESS_TOKEN', 'old-token')
    path = tmp_path / '.env'
    path.write_text('KITE_ACCESS_TOKEN=new-token\n')
    load_local_kite_credentials(path)
    import os
    assert os.environ['KITE_API_KEY'] == 'old-key'
    assert os.environ['KITE_ACCESS_TOKEN'] == 'old-token'


def test_launcher_does_not_override_other_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('MARKET_DATA_SOURCE', 'cache')
    path = tmp_path / '.env'
    path.write_text('KITE_API_KEY=new-key\nKITE_ACCESS_TOKEN=new-token\nMARKET_DATA_SOURCE=kite\n')
    monkeypatch.setenv('KITE_API_KEY', 'old-key')
    monkeypatch.setenv('KITE_ACCESS_TOKEN', 'old-token')
    load_local_kite_credentials(path)
    import os
    assert os.environ['MARKET_DATA_SOURCE'] == 'cache'
