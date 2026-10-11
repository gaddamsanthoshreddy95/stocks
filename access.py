"""Interactive helper for exchanging a Kite request token without embedded secrets."""

import os
from getpass import getpass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException


def parse_request_token(value: str) -> str:
    value=value.strip()
    if '://' in value or value.lstrip('?').startswith('request_token='):
        query=parse_qs(urlparse(value).query if '://' in value else value.lstrip('?'))
        tokens=query.get('request_token',[])
        if len(tokens)!=1 or not tokens[0].strip():
            raise ValueError('Paste the final redirect URL containing one request_token, not an intermediate login URL.')
        return tokens[0].strip()
    return value


def main() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    api_key = os.getenv("KITE_API_KEY") or getpass("Kite API key: ")
    api_secret = os.getenv("KITE_API_SECRET") or getpass("Kite API secret: ")
    if not api_key or not api_secret:
        raise SystemExit("API key and API secret are required.")
    kite = KiteConnect(api_key=api_key)
    print("Open this URL and log in to Kite:")
    print(kite.login_url())
    print("Immediately paste the redirect URL or its request_token below.")
    request_token = getpass("Kite redirect URL or request token: ").strip()
    if request_token in (api_key, api_secret):
        raise SystemExit(
            "You pasted an API key or API secret. Complete the browser login "
            "and paste the final redirect URL containing request_token=."
        )
    try:
        request_token=parse_request_token(request_token)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    if not request_token:
        raise SystemExit("A request_token from the completed Kite login is required.")
    try:
        session = kite.generate_session(
            request_token=request_token, api_secret=api_secret)
    except TokenException:
        raise SystemExit(
            "Kite rejected the request token. Run this script again, log in using "
            "the displayed URL, and immediately paste the new redirect URL or "
            "request_token. Request tokens expire within minutes and can only be "
            "used once. If a fresh token still fails, check that KITE_API_KEY and "
            "KITE_API_SECRET belong to the same Kite app."
        ) from None
    access_token = session.get("access_token")
    if not access_token:
        raise SystemExit("Kite did not return an access token.")
    set_key(str(env_path), "KITE_API_KEY", api_key, quote_mode="never")
    set_key(str(env_path), "KITE_ACCESS_TOKEN", access_token, quote_mode="never")
    print("Kite authentication succeeded. KITE_ACCESS_TOKEN was updated in .env.")


if __name__ == "__main__":
    main()
