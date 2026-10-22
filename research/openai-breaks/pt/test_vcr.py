import os, sys, vcr
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from mock import start
from openai import OpenAI

def test_record_then_replay_offline(tmp_path):
    cassette = str(tmp_path / "c.yaml")
    base = start()
    with vcr.use_cassette(cassette, record_mode="once", match_on=["method", "path"]):
        OpenAI(api_key="x", base_url=base, max_retries=0).models.list()
    text = open(cassette).read() if os.path.exists(cassette) else ""
    assert "/v1/models" in text, "cassette has no recorded request: SDK traffic bypassed vcr"
    dead = "http://127.0.0.1:9/v1"  # nothing listens here
    with vcr.use_cassette(cassette, record_mode="none", match_on=["method", "path"]):
        ids = [m.id for m in OpenAI(api_key="x", base_url=dead, max_retries=0).models.list()]
    assert ids == ["m"]
