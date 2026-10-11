from unittest.mock import Mock
from kiteconnect.exceptions import TokenException
from scripts.check_kite_auth import probe


def test_diagnostic_uses_read_only_profile():
    client=Mock()
    assert probe(client)=={'status':'ACCEPTED'}
    client.profile.assert_called_once_with()


def test_diagnostic_does_not_expose_exception_or_profile_contents():
    client=Mock()
    client.profile.side_effect=TokenException('sensitive credential text')
    result=probe(client)
    assert result=={'status':'FAILED','error_type':'TokenException'}
    assert 'sensitive' not in str(result)
