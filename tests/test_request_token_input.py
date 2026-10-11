import pytest
from access import parse_request_token


@pytest.mark.parametrize('value',[
    'example-token','request_token=example-token',
    '?request_token=example-token&status=success',
    'http://127.0.0.1/?request_token=example-token&action=login',
])
def test_request_token_input_forms(value):
    assert parse_request_token(value)=='example-token'


@pytest.mark.parametrize('value',[
    'http://127.0.0.1/?sess_id=intermediate',
    'request_token=',
    'request_token=one&request_token=two',
])
def test_invalid_redirect_does_not_become_an_access_token(value):
    with pytest.raises(ValueError):parse_request_token(value)


def test_login_exchanges_request_token_and_saves_returned_access_token(monkeypatch):
    import access
    from unittest.mock import Mock
    monkeypatch.setenv('KITE_API_KEY','example-key')
    monkeypatch.setenv('KITE_API_SECRET','example-secret')
    monkeypatch.setattr(access,'load_dotenv',Mock())
    monkeypatch.setattr(access,'getpass',lambda prompt:'request_token=example-request')
    client=Mock();client.generate_session.return_value={'access_token':'returned-access'}
    monkeypatch.setattr(access,'KiteConnect',Mock(return_value=client))
    save=Mock();monkeypatch.setattr(access,'set_key',save)
    access.main()
    client.generate_session.assert_called_once_with(request_token='example-request',api_secret='example-secret')
    saved={call.args[1]:call.args[2] for call in save.call_args_list}
    assert saved=={'KITE_API_KEY':'example-key','KITE_ACCESS_TOKEN':'returned-access'}
