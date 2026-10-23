"""What Chief speaks out loud must never be symbols, tables or long links."""

from __future__ import annotations

import pytest

from chief.speech import MAX_SPOKEN, ON_SCREEN, spoken

BRIEF = """# Meeting Brief: Acme — Dana
**Wednesday, 15:00 | 60 min**

---

## History

| Date | Event |
|------|-------|
| Oct 7 | Dana sent security questionnaire |
| Oct 13 | Kickoff call held |

## Open Questions

1. **Quote delivered?** confirm sent
"""


def test_a_markdown_brief_is_spoken_as_its_title_and_a_pointer_to_the_screen():
    out = spoken(BRIEF)
    assert out == f"Meeting Brief: Acme, Dana. {ON_SCREEN}"
    for junk in ("|", "---", "#", "*"):
        assert junk not in out


def test_a_short_plain_reply_is_unchanged():
    assert spoken("Done. 4 change(s) made.") == "Done. 4 change(s) made."
    assert spoken("Okay, skipped.") == "Okay, skipped."


def test_links_are_spoken_as_the_site_not_the_address():
    out = spoken("Train 526 departs SF at 6:20 PM per https://www.caltrain.com/schedules/weekday-timetable — check for holiday changes. "
                 "Boba Guys is on the way: https://maps.apple.com/place?place-id=IE4F74DEE86538636.")
    assert "https" not in out and "/" not in out and "?" not in out.replace("?.", "")
    assert "caltrain.com" in out and "maps.apple.com" in out and "—" not in out


def test_a_bulleted_list_is_read_as_sentences_not_as_dashes_and_colons():
    out = spoken("I read a flyer and found 1 thing(s) for your calendar:\n- Startup Mixer: Thu Nov 12, 6:00 PM to 8:00 PM at The Foundry\nAdd it? Say yes.")
    assert out == "I read a flyer and found 1 thing(s) for your calendar: Startup Mixer: Thu Nov 12, 6:00 PM to 8:00 PM at The Foundry. Add it? Say yes."
    assert ":," not in out and ". -" not in out


def test_an_inbox_style_reply_loses_its_dashes_and_marks():
    out = spoken("3 to answer: Sam Rivera (Deck for Friday? — by Friday), Maya Chen (Re: Q4 roadmap numbers).\nDeadline: Invoice 1042 — due Oct 30.")
    assert "—" not in out and "\n" not in out
    assert "Sam Rivera" in out and "Oct 30" in out


def test_a_long_reply_is_cut_at_a_sentence_and_points_to_the_screen():
    long = " ".join(f"Sentence number {i} says something useful." for i in range(60))
    out = spoken(long)
    assert len(out) <= MAX_SPOKEN + len(ON_SCREEN) + 2 and out.endswith(ON_SCREEN)
    assert "Sentence number 0" in out and "Sentence number 59" not in out
    assert ".." not in out  # a cut that ends on a full stop must not get a second one


@pytest.mark.parametrize("text", ["", "   ", None])
def test_empty_is_silent(text):
    assert spoken(text) == ""


def test_a_table_only_reply_is_not_read_symbol_by_symbol():
    out = spoken("| a | b |\n| --- | --- |\n| 1 | 2 |")
    assert out == ON_SCREEN
