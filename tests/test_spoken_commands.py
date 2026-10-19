"""Real speech arrives padded with filler words. Approving and skipping must still work, and must stay
plain string matching: nothing a model says can approve anything."""

from __future__ import annotations

import pytest

from chief.intent import _command, handle_utterance


@pytest.mark.parametrize("said", [
    "Do it", "Chief, do it.", "yeah do it please", "okay approve", "Chief do it now", "sounds good", "go ahead",
    "Hey Chief, go ahead please", "ok do it",
])
def test_natural_ways_of_approving(rt, tools, said):
    handle_utterance(rt, "how's my week looking?")
    out = handle_utterance(rt, said)
    assert "change(s) made" in out["say"], said
    assert any(c[0] == "create_event" for c in tools.calls)


@pytest.mark.parametrize("said", [
    "not now", "Chief skip that", "actually never mind", "no thanks", "skip it", "Chief, cancel", "nah",
])
def test_natural_ways_of_skipping(rt, tools, said):
    handle_utterance(rt, "how's my week looking?")
    out = handle_utterance(rt, said)
    assert out["say"] == "Okay, skipped.", said
    assert tools.calls == []
    assert rt.gate.pending == {}


def test_approve_words_do_nothing_when_nothing_is_pending(rt, tools):
    out = handle_utterance(rt, "Chief, do it please")
    assert tools.calls == []
    assert "I can help with" in out["say"]


@pytest.mark.parametrize("said", [
    "That sounds good. Go ahead.", "Perfect. Add it.", "Great, go for it", "That looks good, do it", "Okay, that sounds good, go ahead please",
    "Nice. Do it.",
])
def test_a_praise_opener_before_the_yes_still_approves(rt, tools, said):
    handle_utterance(rt, "how's my week looking?")
    out = handle_utterance(rt, said)
    assert "change(s) made" in out["say"], said
    assert any(c[0] == "create_event" for c in tools.calls)


def test_praise_alone_or_with_something_else_is_not_an_approval(rt, tools):
    handle_utterance(rt, "how's my week looking?")
    for said in ("That sounds good but skip it", "Perfect timing, what's the weather"):
        handle_utterance(rt, said)
        assert tools.calls == [], said
    assert len(rt.gate.pending) == 1  # still waiting; "but skip it" is not a clean skip either


def test_a_muddled_yes_asks_again_instead_of_letting_the_model_improvise(rt, tools):
    handle_utterance(rt, "how's my week looking?")
    out = handle_utterance(rt, "Hmm I guess you could maybe go ahead with that someday")
    assert 'Say "do it"' in out["say"] and out["proposal"] is None
    assert tools.calls == [] and len(rt.gate.pending) == 1


def test_the_yes_guard_does_not_block_a_real_request(rt, tools):
    handle_utterance(rt, "how's my week looking?")
    out = handle_utterance(rt, "go ahead and tell me what's in my inbox")
    assert out["proposal"] is not None and out["proposal"].skill == "email_brief"  # a trigger request is still a request
    assert 'Say "do it"' not in out["say"]


def test_stripping_never_empties_a_command():
    assert _command("please") == "please"
    assert _command("chief") == "chief"
    assert _command("yes") == "yes"
    assert _command("sounds good") == "sounds good" and _command("perfect") == "perfect"  # a lone praise word is left as spoken
    assert _command("that sounds good go ahead") == "go ahead" and _command("perfect add it") == "add it"


def test_a_request_with_filler_still_reaches_its_skill(rt):
    out = handle_utterance(rt, "Chief, could you tell me how's my week looking please")
    assert out["proposal"] is not None and out["proposal"].skill == "audit"
