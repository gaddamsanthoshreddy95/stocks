"""Interactive helper for exchanging a Kite request token without embedded secrets."""

import os
from getpass import getpass

from dotenv import load_dotenv
from kiteconnect import KiteConnect


def main() -> None:
    load_dotenv()
    api_key = os.getenv("KITE_API_KEY") or getpass("Kite API key: ")
    api_secret = os.getenv("KITE_API_SECRET") or getpass("Kite API secret: ")
    request_token = getpass("Kite request token: ")
    if not api_key or not api_secret or not request_token:
        raise SystemExit("API key, API secret, and request token are required.")
    session = KiteConnect(api_key=api_key).generate_session(
        request_token=request_token, api_secret=api_secret)
    access_token = session.get("access_token")
    if not access_token:
        raise SystemExit("Kite did not return an access token.")
    print("Kite authentication succeeded. Add the returned access token to KITE_ACCESS_TOKEN "
          "in your local .env file. The token is intentionally not printed by this helper.")


if __name__ == "__main__":
    main()
