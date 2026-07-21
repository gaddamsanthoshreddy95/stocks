from kiteconnect import KiteConnect

api_key = "jv3alf8aezsru0u3"
api_secret = "sdlf7jnoahkjn8kb2xiq69trwk3eymv7"

request_token = "g6IkaNkLtYEjl6ddL0bl7PWBa58FGU2D"

kite = KiteConnect(api_key=api_key)

data = kite.generate_session(
    request_token=request_token,
    api_secret=api_secret
)

print(data)