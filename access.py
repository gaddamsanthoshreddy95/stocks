"""Interactive helper for exchanging a Kite request token without embedded secrets."""

import os
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect


def main() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("KITE_API_KEY") or getpass("Kite API key: ")
    api_secret = os.getenv("KITE_API_SECRET") or getpass("Kite API secret: ")
    request_token = getpass("Kite request token: ").strip()
    if not api_key or not api_secret or not request_token:
        raise SystemExit("API key, API secret, and request token are required.")
    session = KiteConnect(api_key=api_key).generate_session(
        request_token=request_token, api_secret=api_secret)
    access_token = session.get("access_token")
    if not access_token:
        raise SystemExit("Kite did not return an access token.")
    set_key(str(env_path), "KITE_ACCESS_TOKEN", access_token, quote_mode="never")
    print("Kite authentication succeeded. KITE_ACCESS_TOKEN was updated in .env.")


if __name__ == "__main__":
    main()
