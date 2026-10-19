"""Post what Chief does to a Slack channel: what you said, each proposal or result with its details and sources, and what
happened to it. Post-only: there are no buttons, because approving stays on the phone page (the Slack approval surface with
buttons is `python -m chief slack`, chief/slack_app.py). Off unless SLACK_BOT_TOKEN and SLACK_CHANNEL are both set.

A Slack problem (no network, wrong channel, a revoked token) never reaches Chief: posts run on a background thread and any
error is logged and dropped. Email subjects and calendar titles appear in these messages, so use a private channel.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

from chief.activity import Activity
from chief.models import Proposal

log = logging.getLogger("chief.slack_post")
_LIMIT = 2900  # Slack caps one section at 3000 characters

_RULE = re.compile(r"^\s*(?:[-*_=]{3,})\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slackify(text: str) -> str:
    """Markdown (what Chief's briefs are written in) to Slack's mrkdwn: no pipes or dashes from tables, bold and headings as
    *bold*, links as <url|text>. Untrusted text is escaped first, so mail or web text cannot add a mention or a link."""
    out: list[str] = []
    for line in _esc(text).splitlines():
        if _RULE.match(line) or (line.lstrip().startswith("|") and _TABLE_SEP.match(line)):
            continue
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|") if c.strip()]
            line = "• " + " — ".join(cells)
        line = re.sub(r"^\s*#+\s*(.+?)\s*$", r"*\1*", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
        out.append(_MD_LINK.sub(r"<\2|\1>", line))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _clip(text: str, limit: int = _LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def proposal_message(p: Proposal) -> tuple[str, list[dict]]:
    """(fallback text, blocks) for one proposal or read-only result."""
    blocks: list[dict] = [{"type": "section", "text": {"type": "mrkdwn", "text": _clip(f"*[{_esc(p.skill)}]*\n{slackify(p.summary)}")}}]
    if p.actions:
        lines = [f"• `{_esc(a.describe()).replace('`', chr(39))}`" for a in p.actions]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _clip("*Will do if approved:*\n" + "\n".join(lines))}})
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ":iphone: Waiting for your approval on the phone."}]})
    if p.sources:
        src = "\n".join(f"• {_esc(s)}" for s in p.sources)
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _clip("Sources:\n" + src)}]})
    return f"Chief [{p.skill}]: {p.summary[:200]}", blocks


class SlackPoster:
    def __init__(self, client: Any, channel: str, background: bool = True):
        self.client, self.channel, self.background = client, channel, background

    def _post(self, text: str, blocks: list[dict] | None = None) -> None:
        def send() -> None:
            try:
                kw: dict[str, Any] = {"channel": self.channel, "text": text}
                if blocks:
                    kw["blocks"] = blocks
                self.client.chat_postMessage(**kw)
            except Exception as exc:  # never break Chief over a chat message
                log.warning("Slack post failed: %s", type(exc).__name__)

        if self.background:
            threading.Thread(target=send, daemon=True, name="chief-slack").start()
        else:
            send()

    def proposal(self, p: Proposal) -> None:
        text, blocks = proposal_message(p)
        self._post(text, blocks)

    def event(self, ev: Activity) -> None:
        """What you said, and what became of a proposal. Everything else on the activity stream is too chatty for a channel."""
        tag = " _(from the recorded cache)_" if ev.detail.get("cached") else ""
        if ev.kind == "heard":
            self._post(f":studio_microphone: *You:* {_esc(ev.title)}")
        elif ev.kind == "approved":
            self._post(f":white_check_mark: *Approved* — {_esc(ev.title.removeprefix('Approved: '))[:300]}{tag}")
        elif ev.kind == "skipped":
            stopped = ev.detail.get("stopped")
            self._post(f":no_entry_sign: *{'Stopped' if stopped else 'Skipped'}* — {_esc(ev.title.split(': ', 1)[-1])[:300]}")


def attach(rt: Any, poster: SlackPoster) -> None:
    """Also post to Slack whatever the runtime shows: each proposal as the gate notifies it, and the stream events above."""
    previous = rt.gate.notify

    def notify(p: Proposal) -> None:
        previous(p)  # the terminal card stays as it was
        poster.proposal(p)

    rt.gate.notify = notify
    rt.bus.subscribe(poster.event)


def attach_from_env(rt: Any, say=print) -> SlackPoster | None:
    """Turn Slack posting on when SLACK_BOT_TOKEN and SLACK_CHANNEL are set. Returns the poster, or None when it is off."""
    token, channel = os.getenv("SLACK_BOT_TOKEN", "").strip(), os.getenv("SLACK_CHANNEL", "").strip()
    if not token or not channel:
        return None
    try:
        from slack_sdk import WebClient  # installed with slack-bolt: pip install -e ".[live]"
    except ImportError:
        say("Slack posting is configured but slack-sdk is not installed. Run: pip install -e .[live]")
        return None
    poster = SlackPoster(WebClient(token=token, timeout=8), channel)
    attach(rt, poster)
    say(f"Posting what Chief does to Slack channel {channel} (no buttons: approve on the phone).")
    return poster
