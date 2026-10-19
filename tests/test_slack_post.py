"""Posting Chief's work to a Slack channel: readable details, escaped untrusted text, never breaks Chief, off by default."""

from __future__ import annotations

import pytest

from chief import slack_post
from chief.activity import Activity
from chief.models import Proposal, ToolCall
from chief.slack_post import SlackPoster, attach, attach_from_env, proposal_message, slackify


class FakeClient:
    def __init__(self, fail=False):
        self.posts, self.fail = [], fail

    def chat_postMessage(self, **kw):
        if self.fail:
            raise ConnectionError("down")
        self.posts.append(kw)


def poster(client=None, **kw):
    return SlackPoster(client or FakeClient(), "C123", background=False, **kw)


def ev(kind, title="", detail=None):
    return Activity(1, "2026-10-19T09:00:00", kind, title, None, detail or {}, True)


BRIEF = """# Meeting Brief: Acme — Dana
**Wednesday, 15:00 | 60 min**

---

| Date | Event |
|------|-------|
| Oct 7 | Dana sent the questionnaire |

1. **Quote delivered?** see [the thread](https://mail.google.com/x?a=1&b=2)
"""


def test_a_markdown_brief_becomes_readable_slack_text():
    out = slackify(BRIEF)
    assert "*Meeting Brief: Acme — Dana*" in out and "*Quote delivered?*" in out  # headings and bold as Slack bold
    assert "• Oct 7 — Dana sent the questionnaire" in out  # a table row is a bullet, the separator is gone
    assert not any(l.lstrip().startswith("|") for l in out.splitlines())  # no table rows left (a | inside a link or prose is fine)
    assert "---" not in out and "**" not in out and "#" not in out
    assert "<https://mail.google.com/x?a=1&amp;b=2|the thread>" in out  # a Slack link, & escaped


def test_untrusted_text_cannot_add_a_mention_or_link():
    out = slackify("Hi <!channel> and <https://evil.example|click> & <@U123>")
    assert "<!channel>" not in out and "<@U123>" not in out and "<https://evil.example|click>" not in out
    assert "&lt;!channel&gt;" in out


def test_a_proposal_shows_summary_actions_sources_and_where_to_approve():
    p = Proposal(skill="trip_planner", summary="Train 526 leaves at 6:20 PM.", key="k",
                 actions=[ToolCall("create_event", {"title": "Caltrain to Palo Alto + boba at Boba Guys", "start": "2026-10-20T18:20:00"})],
                 sources=["https://www.caltrain.com/schedules/weekday-timetable"])
    text, blocks = proposal_message(p)
    flat = str(blocks)
    assert "Train 526 leaves at 6:20 PM." in flat and "Will do if approved" in flat and "create_event(" in flat
    assert "caltrain.com/schedules/weekday-timetable" in flat and "approval on the phone" in flat
    assert not any(b["type"] == "actions" for b in blocks)  # no buttons: the phone approves
    assert text.startswith("Chief [trip_planner]")


def test_a_read_only_result_has_no_waiting_for_approval_line():
    _, blocks = proposal_message(Proposal(skill="email_brief", summary="3 to answer.", key="k", actions=[]))
    assert "approval" not in str(blocks)


def test_a_long_result_is_clipped_to_what_slack_accepts():
    _, blocks = proposal_message(Proposal(skill="meeting_prep", summary="word " * 2000, actions=[], key="k"))
    assert all(len(b["text"]["text"]) <= 3000 for b in blocks if b["type"] == "section")


def test_the_poster_posts_to_its_channel():
    client = FakeClient()
    poster(client).proposal(Proposal(skill="audit", summary="Tuesday has 6 meetings.", actions=[], key="k"))
    assert client.posts[0]["channel"] == "C123" and "Tuesday has 6 meetings." in str(client.posts[0]["blocks"])


@pytest.mark.parametrize("kind,title,detail,expect", [
    ("heard", "How's my week looking?", {}, "You:* How's my week looking?"),
    ("approved", "Approved: Mark 2 async", {}, "Approved* — Mark 2 async"),
    ("skipped", "Skipped: Mark 2 async", {}, "Skipped* — Mark 2 async"),
    ("skipped", "Stopped: Mark 2 async", {"stopped": True}, "Stopped* — Mark 2 async"),
    ("approved", "Approved: x", {"cached": True}, "recorded cache"),
])
def test_what_you_said_and_what_became_of_a_proposal_are_posted(kind, title, detail, expect):
    client = FakeClient()
    poster(client).event(ev(kind, title, detail))
    assert expect in client.posts[0]["text"]


def test_other_activity_is_too_chatty_for_a_channel():
    client = FakeClient()
    p = poster(client)
    for kind in ("step", "working", "read", "write", "say", "proposal", "error"):
        p.event(ev(kind, "x"))
    assert client.posts == []


def test_a_slack_failure_never_reaches_chief():
    poster(FakeClient(fail=True)).proposal(Proposal(skill="audit", summary="x", actions=[], key="k"))  # must not raise
    poster(FakeClient(fail=True)).event(ev("heard", "hi"))


def test_attach_keeps_the_terminal_card_and_adds_slack(rt):
    shown, client = [], FakeClient()
    rt.gate.notify = shown.append
    attach(rt, poster(client))
    from chief.intent import handle_utterance

    handle_utterance(rt, "how's my week looking?")
    assert len(shown) == 1  # still printed where it always was
    texts = [p["text"] for p in client.posts]
    assert any("You:" in t for t in texts) and any(t.startswith("Chief [audit]") for t in texts)
    pid = next(iter(rt.gate.pending))
    rt.gate.approve(pid)
    assert any("Approved*" in p["text"] for p in client.posts)


def test_off_unless_both_the_token_and_the_channel_are_set(rt, monkeypatch):
    for env in ({}, {"SLACK_BOT_TOKEN": "xoxb-fake"}, {"SLACK_CHANNEL": "C1"}):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_CHANNEL", raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        assert attach_from_env(rt, say=lambda s: None) is None


def test_turns_on_with_both_and_never_prints_the_token(rt, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-not-a-real-token")
    monkeypatch.setenv("SLACK_CHANNEL", "C0123ABC")
    made, said = [], []

    class FakeWebClient:
        def __init__(self, token, timeout):
            made.append((token, timeout))

        def chat_postMessage(self, **kw):
            pass

    import slack_sdk

    monkeypatch.setattr(slack_sdk, "WebClient", FakeWebClient)
    posted = attach_from_env(rt, say=said.append)
    assert isinstance(posted, SlackPoster) and made == [("xoxb-not-a-real-token", 8)]
    assert said and "C0123ABC" in said[0] and "xoxb" not in said[0]
