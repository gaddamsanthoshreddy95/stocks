"""Read-only authentication diagnostic; never prints credential values."""
import json
import os
from pathlib import Path
import sys
from dotenv import dotenv_values
from kiteconnect import KiteConnect

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def probe(client):
    try:
        client.profile()
        return {'status':'ACCEPTED'}
    except Exception as exc:
        return {'status':'FAILED','error_type':type(exc).__name__}


def main():
    os.chdir(ROOT)
    values=dotenv_values(ROOT/'.env',interpolate=False)
    key=(values.get('KITE_API_KEY') or '').strip()
    token=(values.get('KITE_ACCESS_TOKEN') or '').strip()
    output={'project':str(ROOT),'env_file':str(ROOT/'.env'),
            'file_key_present':bool(key),'file_token_present':bool(token)}
    if not key or not token:
        output['status']='INCOMPLETE_FILE_CREDENTIALS'
    else:
        client=KiteConnect(api_key=key,timeout=10)
        client.set_access_token(token)
        output['saved_file_credentials']=probe(client)
        from src.providers.kite_provider import KiteProvider
        provider=KiteProvider()
        output['provider_module']=sys.modules[KiteProvider.__module__].__file__
        output['provider_key_matches_file']=provider.kite.api_key==key
        output['provider_token_matches_file']=provider.kite.access_token==token
        output['provider_credentials']=probe(provider.kite)
    print(json.dumps(output,indent=2))


if __name__=='__main__':main()
