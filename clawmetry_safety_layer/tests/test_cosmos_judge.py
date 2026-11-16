"""L3 Cosmos judge client against a fake OpenAI-compatible server (real HTTP on 127.0.0.1): request shape, robust
parsing, cache by frame hash, timeout / garbage -> judge_unavailable."""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from brain.relay import CosmosJudge, parse

GOOD = '{"leaves_go_zone": false, "hazards": ["wet floor sign"], "people_within_2m": 1, "stop": false, "confidence": 0.82, "reason": "dry concourse, one guest"}'


class Fake:
    def __init__(self):
        self.reply = GOOD
        self.delay = 0.0
        self.requests: list[dict] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.requests.append({"path": self.path, "body": body, "auth": self.headers.get("authorization")})
                time.sleep(fake.delay)
                out = json.dumps({"choices": [{"message": {"content": fake.reply}}]}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                try:
                    self.wfile.write(out)
                except BrokenPipeError:
                    pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.srv.shutdown()


def test_request_shape_and_verdict(fake):
    j = CosmosJudge(fake.url, timeout_s=2, api_key="k-cosmos")
    v = j.judge(b"\xff\xd8jpeg-1\xff\xd9", "dry concourse only", "forward 2 m at 0.3 m/s toward bath_entrance")
    assert v["hazards"] == ["wet floor sign"] and v["people_within_2m"] == 1 and v["confidence"] == 0.82
    assert v["cached"] is False and v["latency_s"] >= 0
    r = fake.requests[0]
    assert r["path"] == "/v1/chat/completions" and r["auth"] == "Bearer k-cosmos"
    b = r["body"]
    assert b["model"] == "nvidia/Cosmos-Reason2-2B" and b["temperature"] == 0 and b["max_tokens"] == 150
    content = b["messages"][1]["content"]
    assert content[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert (
        "forward 2 m at 0.3 m/s toward bath_entrance" in content[1]["text"]
        and "dry concourse only" in content[1]["text"]
    )
    assert "<think>" in b["messages"][0]["content"]  # told NOT to produce one


def test_cache_by_frame_hash(fake):
    j = CosmosJudge(fake.url, timeout_s=2)
    j.judge(b"frame-A", "g", "a")
    v = j.judge(b"frame-A", "g", "a")
    assert v["cached"] is True and len(fake.requests) == 1
    j.judge(b"frame-B", "g", "a")
    assert len(fake.requests) == 2


def test_timeout_is_unavailable(fake):
    fake.delay = 1.5
    v = CosmosJudge(fake.url, timeout_s=0.5).judge(b"x", "g", "a")
    assert v["judge_unavailable"] and "timed out" in v["error"].lower()


def test_garbage_is_unavailable(fake):
    fake.reply = "I think the robot should probably be careful."
    v = CosmosJudge(fake.url, timeout_s=2).judge(b"y", "g", "a")
    assert v["judge_unavailable"] and v["error"] == "unparseable reply"


def test_server_down_is_unavailable():
    v = CosmosJudge("http://127.0.0.1:1/v1", timeout_s=1).judge(b"z", "g", "a")
    assert v["judge_unavailable"]


@pytest.mark.parametrize(
    "text,expect",
    [
        (
            '<think>hmm {"stop": true}</think>{"leaves_go_zone": "yes", "stop": "false", "confidence": "0.9", "hazards": "stairs", "people_within_2m": "2"}',
            {"leaves_go_zone": True, "stop": False, "confidence": 0.9, "hazards": ["stairs"], "people_within_2m": 2},
        ),
        (
            '```json\n{"leaves_go_zone": false, "hazards": [], "people_within_2m": 0, "stop": false, "confidence": 1.7, "reason": "a {brace} in text"}\n```',
            {"confidence": 1.0, "reason": "a {brace} in text"},
        ),
        (
            'Answer: {"note": 1} then {"stop": true, "confidence": 0.6}',
            {"stop": True, "leaves_go_zone": False, "hazards": []},
        ),
        ('{"stop": tru', None),
        ("", None),
    ],
)
def test_parse_robust(text, expect):
    v = parse(text)
    if expect is None:
        assert v is None
    else:
        for k, val in expect.items():
            assert v[k] == val, (k, v)
