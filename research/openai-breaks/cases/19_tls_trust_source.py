# Where do the SDK's default HTTPS connections get CA roots from? (no network)
try:
    import httpx2 as h  # what openai 3.x uses
except ImportError:
    import httpx as h
ctx = h.create_ssl_context()
kind = f"{type(ctx).__module__}.{type(ctx).__name__}"
if type(ctx).__module__.startswith("truststore"):
    raise RuntimeError(f"{h.__name__} verifies with the OS trust store ({kind}), not certifi")
print(f"{h.__name__}: {kind} with {len(ctx.get_ca_certs())} CA certs loaded (certifi bundle)")
