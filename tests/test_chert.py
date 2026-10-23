"""iMessage through Chert: send shape, webhook signature, owner-only replies, approval by text, off by default."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path

import httpx
import pytest

from chief.chert import Chert, ChertChannel, ChertError, channel_from_env, parse_inbound, same_handle, verify_signature
from chief.models import Proposal, ToolCall

SECRET = "whsec_test_secret"
PHONE = "+14155551234"
NOW = 1_800_000_000.0


def sign(body: bytes, secret: str = SECRET, ts: float = NOW) -> str:
    return f"t={int(ts)},v1=" + hmac.new(secret.encode(), f"{int(ts)}.".encode() + body, hashlib.sha256).hexdigest()


def inbound(text="yes", sender=PHONE, event_id="evt_1", etype="message.received") -> bytes:
    return json.dumps({
        "event_id": event_id, "event_type": etype,
        "data": {"message": {"id": "m1", "sender_handle": {"handle": sender}, "parts": [{"type": "text", "value": text}]}},
    }).encode()


class Texts:
    """Stands in for Chert.send and records what would have been texted."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def __call__(self, phone, body, key=None):
        self.sent.append((phone, body))
        return {"status": "sent"}


def channel(respond=None, texts=None, secret=SECRET):
    client = Chert("sk_test")
    texts = texts if texts is not None else Texts()
    client.send = texts  # type: ignore[method-assign]
    return ChertChannel(client, PHONE, respond, secret, background=False, clock=lambda: NOW), texts


def deliver(ch, body: bytes, header: str | None = None):
    return ch.handle({"X-Webhook-Signature": header if header is not None else sign(body)}, body)


# ---- sending


def mock_client(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    return Chert("sk_secret", http=httpx.Client(transport=httpx.MockTransport(wrapped))), calls


def test_send_posts_the_documented_body_with_the_bearer_secret():
    c, calls = mock_client(lambda r: httpx.Response(200, json={"status": "sent", "message_id": "m1"}))
    assert c.send(PHONE, "hello", "key-1")["message_id"] == "m1"
    r = calls[0]
    assert str(r.url) == "https://console.trychert.com/api/v1/send" and r.headers["Authorization"] == "Bearer sk_secret"
    assert json.loads(r.content) == {"phone": PHONE, "body": "hello", "idempotency_key": "key-1"}


def test_send_clips_long_text():
    c, calls = mock_client(lambda r: httpx.Response(200, json={"status": "queued"}))
    c.send(PHONE, "x" * 5000)
    assert len(json.loads(calls[0].content)["body"]) == 600


@pytest.mark.parametrize("response", [
    httpx.Response(401, json={}), httpx.Response(403, json={}),
    httpx.Response(422, json={"status": "failed", "error": "send_failed", "reason": "not on iMessage", "retryable": False}),
    httpx.Response(200, json={"status": "failed", "reason": "line saturated"}),
    httpx.Response(500, text="oops"),
])
def test_send_failures_raise_without_leaking_the_secret(response):
    c, _ = mock_client(lambda r: response)
    with pytest.raises(ChertError) as e:
        c.send(PHONE, "x")
    assert "sk_secret" not in str(e.value)


def test_a_network_error_is_a_chert_error():
    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ChertError, match="send failed"):
        mock_client(boom)[0].send(PHONE, "x")


# ---- the webhook signature


def test_a_correct_fresh_signature_verifies():
    body = inbound()
    assert verify_signature(SECRET, sign(body), body, now=NOW)


@pytest.mark.parametrize("make", [
    lambda b: sign(b, secret="someone-elses"),  # wrong secret
    lambda b: sign(b, ts=NOW - 4000),  # replayed from an hour ago
    lambda b: sign(b, ts=NOW + 4000),  # from the future
    lambda b: "",  # missing
    lambda b: "garbage",
    lambda b: f"t={int(NOW)}",  # no signature part
    lambda b: f"t=abc,v1={'0' * 64}",
])
def test_bad_signatures_are_rejected(make):
    body = inbound()
    assert not verify_signature(SECRET, make(body), body, now=NOW)


def test_a_changed_body_fails_its_signature():
    body = inbound("yes")
    assert not verify_signature(SECRET, sign(body), inbound("no"), now=NOW)


# ---- who sent it


@pytest.mark.parametrize("a,b,same", [
    ("+14155551234", "4155551234", True), ("(415) 555-1234", "+1 415 555 1234", True), ("+14155551234", "+14155559999", False),
    ("Me@iCloud.com", "me@icloud.com", True), ("me@icloud.com", "+14155551234", False), ("", "+14155551234", False), ("12", "12", False),
])
def test_same_handle(a, b, same):
    assert same_handle(a, b) is same


def test_parse_inbound_reads_the_documented_fields_and_survives_junk():
    assert parse_inbound(json.loads(inbound("go ahead", "+1415", "e9"))) == ("e9", "+1415", "go ahead")
    assert parse_inbound({}) == ("", "", "")
    assert parse_inbound({"data": {"message": {"parts": [{"type": "image", "attachment_id": "a"}]}}}) == ("", "", "")
    assert parse_inbound({"data": "x"}) == ("", "", "")


# ---- the channel


def test_a_signed_text_from_the_owner_is_answered_by_texting_back():
    heard = []
    ch, texts = channel(respond=lambda t: heard.append(t) or "Done. 4 change(s) made.")
    assert deliver(ch, inbound("go ahead")) == (200, {"accepted": True})
    assert heard == ["go ahead"] and texts.sent == [(PHONE, "Done. 4 change(s) made.")]


def test_an_unsigned_or_wrongly_signed_delivery_is_refused_and_does_nothing():
    heard = []
    ch, texts = channel(respond=lambda t: heard.append(t) or "x")
    assert deliver(ch, inbound(), header="")[0] == 401
    assert deliver(ch, inbound(), header=sign(inbound(), secret="wrong"))[0] == 401
    assert heard == [] and texts.sent == []


def test_a_message_from_anyone_else_is_ignored_with_no_reply():
    heard = []
    ch, texts = channel(respond=lambda t: heard.append(t) or "x")
    body = inbound("do it", sender="+14155559999")
    assert deliver(ch, body) == (200, {"ignored": "sender"})
    assert heard == [] and texts.sent == []


def test_other_events_empty_text_and_replays_are_ignored():
    heard = []
    ch, texts = channel(respond=lambda t: heard.append(t) or "ok")
    assert deliver(ch, inbound(etype="message.delivered"))[1] == {"ignored": "event"}
    assert deliver(ch, inbound(text="   "))[1] == {"ignored": "sender"}
    assert deliver(ch, inbound("yes", event_id="e7"))[1] == {"accepted": True}
    assert deliver(ch, inbound("yes", event_id="e7"))[1] == {"ignored": "duplicate"}  # Chert retried: acted on once
    assert heard == ["yes"]


def test_bad_json_is_a_400_and_a_failing_handler_does_not_break_the_webhook():
    ch, texts = channel(respond=lambda t: 1 / 0)
    body = b"not json"
    assert deliver(ch, body)[0] == 400
    assert deliver(ch, inbound("yes"))[0] == 200 and texts.sent == [(PHONE, "Sorry, something went wrong.")]


def test_without_a_webhook_secret_inbound_is_off():
    ch, _ = channel(respond=lambda t: "x", secret="")
    assert deliver(ch, inbound())[0] == 404


def test_only_proposals_that_need_a_yes_are_texted_and_they_read_cleanly():
    ch, texts = channel()
    ch.notify(Proposal(skill="email_brief", summary="3 to answer.", actions=[], key="a"))
    assert texts.sent == []  # a read-only answer is for the screen
    p = Proposal(skill="audit", summary="**Tuesday** has 6 meetings. I can mark 2 as async — approve?", key="b",
                 actions=[ToolCall("patch_event", {"event_id": "e1", "fields": {"summary": "[Async] x"}})])
    ch.notify(p)
    (phone, body), = texts.sent
    assert phone == PHONE and "Tuesday has 6 meetings" in body and "Reply YES to do it, or NO to skip." in body
    assert "**" not in body and "—" not in body


def test_a_chert_outage_never_breaks_a_proposal():
    client = Chert("sk")
    client.send = lambda *a, **k: (_ for _ in ()).throw(ChertError("down"))  # type: ignore[method-assign]
    ch = ChertChannel(client, PHONE, None, "", background=False)
    ch.notify(Proposal(skill="audit", summary="x", key="k", actions=[ToolCall("patch_event", {})]))  # must not raise


# ---- switching it on


def test_off_unless_the_secret_and_your_number_are_both_set(rt, monkeypatch):
    for env in ({}, {"CHERT_SIGNING_SECRET": "sk"}, {"CHIEF_NOTIFY_PHONE": PHONE}):
        monkeypatch.delenv("CHERT_SIGNING_SECRET", raising=False)
        monkeypatch.delenv("CHIEF_NOTIFY_PHONE", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert channel_from_env(rt, None, say=lambda s: None) is None


def test_switching_on_keeps_the_terminal_card_and_hides_the_number(rt, monkeypatch):
    monkeypatch.setenv("CHERT_SIGNING_SECRET", "sk_real_looking")
    monkeypatch.setenv("CHIEF_NOTIFY_PHONE", PHONE)
    shown, said = [], []
    rt.gate.notify = shown.append
    ch = channel_from_env(rt, lambda t: "x", say=said.append)
    sent = Texts()
    ch.client.send = sent  # type: ignore[method-assign]
    ch.background = False
    from chief.intent import handle_utterance

    handle_utterance(rt, "how's my week looking?")
    assert len(shown) == 1 and len(sent.sent) == 1 and sent.sent[0][0] == PHONE
    assert said and PHONE not in said[0] and "sk_real" not in said[0] and "send only" in said[0]  # no CHERT_WEBHOOK_SECRET yet


# ---- end to end through the web server


def test_a_signed_text_yes_approves_the_waiting_proposal_through_the_normal_gate(rt, tools, monkeypatch):
    from fastapi.testclient import TestClient

    from chief.web.app import create_app

    monkeypatch.setenv("CHERT_SIGNING_SECRET", "sk_test")
    monkeypatch.setenv("CHIEF_NOTIFY_PHONE", PHONE)
    monkeypatch.setenv("CHERT_WEBHOOK_SECRET", SECRET)
    texts = Texts()
    monkeypatch.setattr(Chert, "send", lambda self, phone, body, key=None: texts(phone, body, key))
    monkeypatch.setattr("chief.chert.ChertChannel._text", lambda self, body, key=None: texts(self.phone, body, key))
    client = TestClient(create_app(rt, "phone-token", transcriber=None))
    client.post("/ask", json={"text": "how's my week looking?"}, headers={"X-Chief-Token": "phone-token"})
    assert len(rt.gate.pending) == 1 and tools.calls == []

    def hook(body: bytes, header: str | None):
        return client.post("/chert/webhook", content=body, headers={"X-Webhook-Signature": header} if header is not None else {})

    def signed(body: bytes, ts: float | None = None):
        return sign(body, ts=ts if ts is not None else time.time())

    stranger = inbound("yes", sender="+14155559999", event_id="e1")
    assert hook(stranger, signed(stranger)).status_code == 200 and len(rt.gate.pending) == 1  # not you: nothing happens
    mine = inbound("go ahead", event_id="e2")
    assert hook(mine, None).status_code == 401 and len(rt.gate.pending) == 1  # no signature: refused
    assert hook(mine, sign(mine, ts=time.time() - 4000)).status_code == 401  # a replay from an hour ago
    assert hook(mine, signed(mine)).status_code == 200
    deadline = time.time() + 5  # the approval and the reply text both happen on a background thread
    while time.time() < deadline and not (not rt.gate.pending and any("change(s) made" in b for _, b in texts.sent)):
        time.sleep(0.05)
    assert rt.gate.pending == {} and tools.calls  # approved by text, through the same gate and the same plain matching
    assert any("change(s) made" in body for _, body in texts.sent)


def test_the_webhook_route_is_a_404_when_inbound_is_not_configured(rt, monkeypatch):
    from fastapi.testclient import TestClient

    from chief.web.app import create_app

    for k in ("CHERT_SIGNING_SECRET", "CHIEF_NOTIFY_PHONE", "CHERT_WEBHOOK_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert TestClient(create_app(rt, "t")).post("/chert/webhook", content=b"{}").status_code == 404


# ---- the subscribe script


def load_script():
    spec = importlib.util.spec_from_file_location("chert_subscribe", Path(__file__).resolve().parent.parent / "scripts" / "chert_subscribe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_subscribe_saves_the_secret_to_env_without_printing_it(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\nCHERT_WEBHOOK_SECRET=old\nB=2\n", encoding="utf-8")
    monkeypatch.setenv("CHERT_SIGNING_SECRET", "sk_test")
    calls, said = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(201, json={"id": "sub1", "secret": "whsec_shown_once"})

    code = load_script().main(["https://x.trycloudflare.com/chert/webhook"], http=httpx.Client(transport=httpx.MockTransport(handler)),
                              env_path=env, say=said.append)
    assert code == 0 and json.loads(calls[0].content) == {"url": "https://x.trycloudflare.com/chert/webhook", "events": ["message.received"]}
    assert calls[0].headers["Authorization"] == "Bearer sk_test"
    assert env.read_text("utf-8") == "A=1\nCHERT_WEBHOOK_SECRET=whsec_shown_once\nB=2\n"  # replaced, not appended twice
    assert "whsec_shown_once" not in " ".join(said)


@pytest.mark.parametrize("args", [[], ["http://insecure.example/chert/webhook"], ["https://x.example/other"], ["a", "b"]])
def test_subscribe_refuses_a_bad_address(args, tmp_path):
    assert load_script().main(args, env_path=tmp_path / ".env", say=lambda s: None) == 2


def test_subscribe_reports_a_refusal_and_a_missing_secret(tmp_path, monkeypatch):
    mod = load_script()
    monkeypatch.setenv("CHERT_SIGNING_SECRET", "sk_test")
    said = []
    refuse = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(422, json={"reason": "url must be https"})))
    assert mod.main(["https://x.example/chert/webhook"], http=refuse, env_path=tmp_path / ".env", say=said.append) == 1 and "url must be https" in said[0]
    nosecret = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(201, json={"id": "s"})))
    assert mod.main(["https://x.example/chert/webhook"], http=nosecret, env_path=tmp_path / ".env", say=said.append) == 1
    assert not (tmp_path / ".env").exists()
