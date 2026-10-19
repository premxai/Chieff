from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from chief.web.app import WhisperTranscriber, create_app  # noqa: E402

TOKEN = "s3cret-test-token"
H = {"X-Chief-Token": TOKEN}


class FakeSTT:
    def __init__(self, text="how's my week looking", fail=False, enabled=True):
        self.text, self.fail, self.enabled, self.calls = text, fail, enabled, []

    def transcribe(self, audio, filename, content_type):
        self.calls.append((len(audio), filename, content_type))
        if self.fail:
            raise RuntimeError("upstream down")
        return self.text


@pytest.fixture
def stt():
    return FakeSTT()


@pytest.fixture
def client(rt, stt):
    return TestClient(create_app(rt, TOKEN, transcriber=stt))


def test_every_route_needs_the_token(client):
    for method, path in [
        ("get", "/"),
        ("get", "/status"),
        ("post", "/ask"),
        ("post", "/stt"),
        ("post", "/approve/abc"),
        ("post", "/skip/abc"),
        ("post", "/stop"),
    ]:
        assert getattr(client, method)(path).status_code == 401, path
        assert getattr(client, method)(path, headers={"X-Chief-Token": "nope"}).status_code == 401, path


def test_token_via_query_header_and_bearer(client):
    assert client.get("/status", params={"token": TOKEN}).status_code == 200
    assert client.get("/status", headers=H).status_code == 200
    assert client.get("/status", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_empty_token_refuses_to_start(rt):
    with pytest.raises(ValueError):
        create_app(rt, "")


def test_page_is_served(client):
    r = client.get("/", params={"token": TOKEN})
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "speechSynthesis" in r.text and "/status" in r.text
    assert "EventSource" not in r.text  # polling only: quick tunnels cannot carry SSE


def test_ask_then_approve_through_the_gate(client, rt, tools):
    out = client.post("/ask", json={"text": "how's my week looking?"}, headers=H).json()
    assert out["proposal"]["skill"] == "audit" and out["steps"][0] == "Running audit"
    pid = out["proposal"]["id"]

    status = client.get("/status", headers=H).json()
    assert [p["id"] for p in status["pending"]] == [pid]
    assert status["say"] == out["say"] and status["steps"] == out["steps"]
    assert tools.calls == []  # nothing ran before approval

    done = client.post(f"/approve/{pid}", headers=H).json()
    assert "change(s) made" in done["say"]
    assert tools.calls  # the write happened, via the gate
    status = client.get("/status", headers=H).json()
    assert status["pending"] == [] and status["steps"][-1] == "Done"


def test_replies_carry_a_clean_version_to_speak(client):
    out = client.post("/ask", json={"text": "how's my week looking?"}, headers=H).json()
    assert out["speak"] and "|" not in out["speak"] and "---" not in out["speak"]
    pid = out["proposal"]["id"]
    assert client.post(f"/skip/{pid}", headers=H).json()["speak"] == "Okay, skipped."


def test_the_page_has_a_stop_button_that_cancels_speech_and_the_request(client):
    html = client.get("/", params={"token": TOKEN}).text
    assert 'id="stop"' in html and "speechSynthesis.cancel()" in html and "AbortController" in html and '"/stop"' in html
    assert "out.speak || out.say" in html  # reads the clean version, shows the full text


def test_stop_with_nothing_running_does_nothing(client):
    assert client.post("/stop", headers=H).json() == {"stopped": False}


def test_stop_during_a_request_drops_its_answer_and_proposals_and_remembers_nothing(rt, tools, stt, monkeypatch):
    import threading

    import chief.web.app as web

    started, release = threading.Event(), threading.Event()
    real = web.handle_utterance

    def slow(rt_, text):
        out = real(rt_, text)  # the audit proposal is made ...
        started.set()
        release.wait(5)  # ... and the request is still running when Stop is pressed
        return out

    monkeypatch.setattr(web, "handle_utterance", slow)
    c = TestClient(create_app(rt, TOKEN, transcriber=stt))
    result = {}
    t = threading.Thread(target=lambda: result.update(c.post("/ask", json={"text": "how's my week looking?"}, headers=H).json()))
    t.start()
    assert started.wait(5)
    stopped = c.post("/stop", headers=H).json()  # answers while the ask still holds the request lock
    release.set()
    t.join(5)
    assert stopped == {"stopped": True}
    assert result["stopped"] is True and result["say"] == "Stopped." and result["proposal"] is None
    assert rt.gate.pending == {}  # the proposal it made is gone ...
    assert not rt.memory.decisions("audit")  # ... and not remembered as a rejection
    assert tools.calls == []  # nothing was written
    again = c.post("/ask", json={"text": "how's my week looking?"}, headers=H).json()
    assert again["proposal"] is not None  # asking again proposes it again


def test_skip_runs_nothing(client, rt, tools):
    pid = client.post("/ask", json={"text": "audit"}, headers=H).json()["proposal"]["id"]
    out = client.post(f"/skip/{pid}", headers=H).json()
    assert out["say"] == "Okay, skipped." and tools.calls == []
    assert client.get("/status", headers=H).json()["pending"] == []


def test_unknown_proposal_is_404(client):
    assert client.post("/approve/nope", headers=H).status_code == 404
    assert client.post("/skip/nope", headers=H).status_code == 404


def test_spoken_approval_goes_through_ask(client, tools):
    client.post("/ask", json={"text": "why am I so tired"}, headers=H)
    out = client.post("/ask", json={"text": "Do it."}, headers=H).json()
    assert "change(s) made" in out["say"] and tools.calls


def test_ask_rejects_empty_text(client):
    assert client.post("/ask", json={"text": "  "}, headers=H).status_code == 400


def test_status_seq_bumps_per_reply(client):
    a = client.get("/status", headers=H).json()["seq"]
    client.post("/ask", json={"text": "hello there"}, headers=H)
    assert client.get("/status", headers=H).json()["seq"] == a + 1


def test_stt_uses_the_transcriber(client, stt):
    r = client.post("/stt", headers=H, files={"audio": ("speech", b"\x1aE\xdf\xa3fake", "audio/webm;codecs=opus")})
    assert r.status_code == 200 and r.json() == {"text": "how's my week looking"}
    assert stt.calls == [(8, "speech.webm", "audio/webm;codecs=opus")]


def test_stt_picks_extension_from_content_type(client, stt):
    client.post("/stt", headers=H, files={"audio": ("speech", b"data", "audio/mp4")})
    assert stt.calls[-1][1] == "speech.mp4"


def test_stt_empty_upload(client):
    assert client.post("/stt", headers=H, files={"audio": ("speech", b"", "audio/webm")}).status_code == 400


def test_stt_upstream_failure_is_502_without_leaking_details(rt):
    c = TestClient(create_app(rt, TOKEN, transcriber=FakeSTT(fail=True)))
    r = c.post("/stt", headers=H, files={"audio": ("speech", b"data", "audio/webm")})
    assert r.status_code == 502 and "upstream down" not in r.text


def test_stt_not_configured_is_503(rt):
    c = TestClient(create_app(rt, TOKEN, transcriber=FakeSTT(enabled=False)))
    r = c.post("/stt", headers=H, files={"audio": ("speech", b"data", "audio/webm")})
    assert r.status_code == 503 and "CHIEF_STT_API_KEY" in r.json()["detail"]
    assert c.get("/status", headers=H).json()["stt"] is False


def test_whisper_transcriber_reads_env(monkeypatch):
    monkeypatch.delenv("CHIEF_STT_BASE_URL", raising=False)
    monkeypatch.delenv("CHIEF_STT_MODEL", raising=False)
    monkeypatch.delenv("CHIEF_STT_API_KEY", raising=False)
    t = WhisperTranscriber.from_env()
    assert t.base_url == "https://api.groq.com/openai/v1" and t.model == "whisper-large-v3-turbo"
    assert not t.enabled
    monkeypatch.setenv("CHIEF_STT_API_KEY", "k")
    monkeypatch.setenv("CHIEF_STT_BASE_URL", "http://stt.test/v1/")
    t = WhisperTranscriber.from_env()
    assert t.enabled and t.base_url == "http://stt.test/v1"


def test_whisper_transcriber_posts_multipart(monkeypatch):
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": " hello "}

    def fake_post(url, headers, data, files, timeout):
        seen.update(url=url, headers=headers, data=data, files=files)
        return Resp()

    monkeypatch.setattr("chief.web.app.httpx.post", fake_post)
    t = WhisperTranscriber("http://stt.test/v1", "k", "whisper-x")
    assert t.transcribe(b"abc", "speech.webm", "audio/webm") == "hello"
    assert seen["url"] == "http://stt.test/v1/audio/transcriptions"
    assert seen["headers"] == {"Authorization": "Bearer k"} and seen["data"]["model"] == "whisper-x"
    assert seen["files"]["file"] == ("speech.webm", b"abc", "audio/webm")
