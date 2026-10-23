"""iMessage through Chert (trychert.com): Chief texts you what it wants to do, and your reply comes back as if you had said it.

Built from Chert's public messaging docs (docs.trychert.com/api, read 2026-10-23). NOT yet run against a real Chert account:
  * send:     POST https://console.trychert.com/api/v1/send   {"phone", "body", "idempotency_key"}   Authorization: Bearer <signing secret>
  * replies:  a `message.received` webhook (HTTPS), signed `X-Webhook-Signature: t=<unix>,v1=<hex>` where hex is
              HMAC-SHA256(<subscription secret>, "<unix>.<raw body>"); payload data.message.sender_handle.handle and
              data.message.parts[{type: "text", value}].

Safety, in the order it matters:
  1. A webhook is acted on only if its signature checks out (constant-time compare, 5 minute window, each event id once).
  2. Only a message FROM your own number (CHIEF_NOTIFY_PHONE) is ever acted on; anyone else's is ignored without a reply.
  3. What you text goes through exactly the same path as the phone page: approval is plain string matching on your words
     ("yes", "go ahead", "do it"), never a model, and nothing is written without it.
  4. Message text is untrusted data like any other input. Replies are the short spoken version (no tables or links dumped).
Off unless CHERT_SIGNING_SECRET and CHIEF_NOTIFY_PHONE are both set. Inbound also needs CHERT_WEBHOOK_SECRET.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from collections import deque
from typing import Any, Callable

import httpx

from chief.models import Proposal
from chief.speech import spoken

log = logging.getLogger("chief.chert")
BASE = "https://console.trychert.com/api/v1"
TOLERANCE_S = 300
MAX_TEXT = 600  # an iMessage is a text, not a document


class ChertError(RuntimeError):
    pass


class Chert:
    def __init__(self, signing_secret: str, http: httpx.Client | None = None, base: str = BASE, timeout: float = 15.0):
        self.secret, self.base = signing_secret, base.rstrip("/")
        self.http = http or httpx.Client(timeout=timeout)

    @classmethod
    def from_env(cls) -> "Chert | None":
        secret = os.getenv("CHERT_SIGNING_SECRET", "").strip()
        return cls(secret, base=os.getenv("CHERT_API_BASE") or BASE) if secret else None

    def send(self, phone: str, body: str, idempotency_key: str | None = None) -> dict:
        """Text `body` to `phone` (E.164, or an Apple ID email). Returns Chert's reply ({"status": "sent"|"queued", "message_id", ...})."""
        payload: dict[str, Any] = {"phone": phone, "body": body[:MAX_TEXT]}
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        try:
            r = self.http.post(f"{self.base}/send", headers={"Authorization": f"Bearer {self.secret}"}, json=payload)
        except httpx.HTTPError as exc:
            raise ChertError(f"Chert send failed: {type(exc).__name__}") from exc
        if r.status_code in (401, 403):
            raise ChertError("Chert refused the request; check CHERT_SIGNING_SECRET")
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 400 or data.get("status") == "failed":
            raise ChertError(f"Chert could not send: {data.get('reason') or data.get('code') or r.status_code}")
        return data


# ----------------------------------------------------------------------------------------------- inbound webhooks


def verify_signature(secret: str, header: str, raw_body: bytes, now: float | None = None, tolerance: int = TOLERANCE_S) -> bool:
    """`header` is `t=<unix>,v1=<hex>`. True only for a fresh, correctly signed body."""
    try:
        parts = dict(p.split("=", 1) for p in (header or "").split(",") if "=" in p)
        ts, given = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        return False
    want = hmac.new(secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, given.lower())


def parse_inbound(payload: dict) -> tuple[str, str, str]:
    """(event id, sender handle, text) of a message.received payload; "" for anything missing."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    msg = data.get("message") if isinstance(data.get("message"), dict) else {}
    sender = (msg.get("sender_handle") or {}).get("handle") if isinstance(msg.get("sender_handle"), dict) else ""
    parts = msg.get("parts") if isinstance(msg.get("parts"), list) else []
    text = " ".join(str(p.get("value", "")) for p in parts if isinstance(p, dict) and p.get("type") == "text")
    return str(payload.get("event_id") or ""), str(sender or ""), " ".join(text.split())


def same_handle(a: str, b: str) -> bool:
    """The same person: phone numbers compare by their last ten digits, email handles case-insensitively."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return False
    if "@" in a or "@" in b:
        return a.lower() == b.lower()
    da, db = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    return len(da) >= 7 and da[-10:] == db[-10:]


# ----------------------------------------------------------------------------------------------- the channel


class ChertChannel:
    def __init__(
        self,
        client: Chert,
        phone: str,
        respond: Callable[[str], str] | None = None,
        webhook_secret: str = "",
        background: bool = True,
        clock: Callable[[], float] = time.time,
    ):
        self.client, self.phone, self.respond, self.webhook_secret = client, phone, respond, webhook_secret
        self.background, self.clock = background, clock
        self._seen: deque[str] = deque(maxlen=200)
        self._lock = threading.Lock()

    @property
    def inbound(self) -> bool:
        return bool(self.webhook_secret and self.respond)

    def _text(self, body: str, key: str | None = None) -> None:
        def go() -> None:
            try:
                self.client.send(self.phone, body, key)
            except Exception as exc:  # a text that does not go out must never break Chief
                log.warning("Chert text failed: %s", type(exc).__name__)

        threading.Thread(target=go, daemon=True, name="chief-chert").start() if self.background else go()

    def notify(self, p: Proposal) -> None:
        """A proposal that needs a yes is texted to you. Read-only answers are not (they are for the screen)."""
        if not p.actions:
            return
        self._text(f"Chief: {spoken(p.summary)} Reply YES to do it, or NO to skip.", f"proposal-{p.id}")

    def handle(self, headers: dict[str, str], raw_body: bytes) -> tuple[int, dict]:
        """A webhook delivery -> (HTTP status, body). Answers fast; the reply to you is sent in the background."""
        if not self.inbound:
            return 404, {"detail": "not enabled"}
        h = {k.lower(): v for k, v in headers.items()}
        if not verify_signature(self.webhook_secret, h.get("x-webhook-signature", ""), raw_body, self.clock()):
            return 401, {"detail": "bad signature"}
        try:
            payload = json.loads(raw_body)
        except ValueError:
            return 400, {"detail": "not json"}
        if not isinstance(payload, dict) or payload.get("event_type") != "message.received":
            return 200, {"ignored": "event"}
        event_id, sender, text = parse_inbound(payload)
        with self._lock:
            if event_id and event_id in self._seen:
                return 200, {"ignored": "duplicate"}
            if event_id:
                self._seen.append(event_id)
        if not same_handle(sender, self.phone) or not text:
            log.info("Chert: ignored a message that is not from the owner or has no text")
            return 200, {"ignored": "sender"}

        def reply() -> None:
            try:
                answer = self.respond(text) or "Done."
            except Exception as exc:
                log.warning("Chert reply failed: %s", type(exc).__name__)
                answer = "Sorry, something went wrong."
            self._text(answer, f"reply-{event_id}" if event_id else None)

        threading.Thread(target=reply, daemon=True, name="chief-chert-reply").start() if self.background else reply()
        return 200, {"accepted": True}


def channel_from_env(rt: Any, respond: Callable[[str], str] | None, say: Callable[[str], None] = print) -> ChertChannel | None:
    """Turn the iMessage channel on when CHERT_SIGNING_SECRET and CHIEF_NOTIFY_PHONE are set: proposals are texted to you.
    If CHERT_WEBHOOK_SECRET is also set, your replies are accepted at POST /chert/webhook."""
    client, phone = Chert.from_env(), os.getenv("CHIEF_NOTIFY_PHONE", "").strip()
    if client is None or not phone:
        return None
    channel = ChertChannel(client, phone, respond, os.getenv("CHERT_WEBHOOK_SECRET", "").strip())
    previous = rt.gate.notify

    def notify(p: Proposal) -> None:
        previous(p)
        channel.notify(p)

    rt.gate.notify = notify
    say(f"Texting proposals to {phone[:3]}***{phone[-2:]} through Chert iMessage"
        + (" (replies accepted at /chert/webhook)." if channel.inbound else " (send only: set CHERT_WEBHOOK_SECRET to accept replies)."))
    return channel
