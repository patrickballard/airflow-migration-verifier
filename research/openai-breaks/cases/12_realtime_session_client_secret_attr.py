# Reading .client_secret off a session response that still contains it
from openai.types.realtime.realtime_session_create_response import RealtimeSessionCreateResponse
r = RealtimeSessionCreateResponse.construct(**{"type": "realtime", "client_secret": {"value": "ek_1", "expires_at": 0}})
v = r.client_secret["value"] if isinstance(r.client_secret, dict) else r.client_secret.value
print("client_secret:", v)
