# This module never imports the SDK, so the agent must leave it alone even
# though it uses the beta path on an injected client.


def extract(client, text):
    return client.beta.chat.completions.parse(model="m", messages=[], response_format=None)
