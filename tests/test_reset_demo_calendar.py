"""The demo reset only undoes what the demo changed, and only on a demo account."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

from chief.models import Event

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("reset_demo_calendar", _ROOT / "scripts" / "reset_demo_calendar.py")
reset = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(reset)
seed = reset.seed

DAY = datetime(2026, 10, 19)  # a Monday: the seeded heavy day is Tuesday the 20th
DEMO_ENV = {"CHIEF_DEMO_GMAIL": "chief.demo.2026@gmail.com"}


class FakeTools:
    def __init__(self, events):
        self.events, self.patched, self.deleted = events, [], []

    def list_events(self, start, end):
        return [e for e in self.events if start <= e.start < end]

    def patch_event(self, event_id, fields):
        self.patched.append((event_id, fields))

    def delete_event(self, event_id):
        self.deleted.append(event_id)


def seeded_events():
    return [Event(f"g-{s.key}", s.title, s.start, s.end, s.description, list(s.attendees)) for s in seed.build_events(DAY)]


def tuesday(events, title):
    return next(e for e in events if e.start == datetime(2026, 10, 20, 14, 0) and e.title == title)


def run(tools, *argv, env=DEMO_ENV):
    lines: list[str] = []
    code = reset.main(list(argv), tools=tools, write=lines.append, env=env)
    return code, "\n".join(lines)


def changed_calendar():
    """Tuesday after the audit was approved and the trip added, plus an event of the user's own."""
    events = seeded_events()
    for e in events:
        if e.start == datetime(2026, 10, 20, 14, 0):
            e.title, e.description = "[Async] Vendor sync", "Chief suggests handling this as a written update instead of a live call."
        if e.start == datetime(2026, 10, 20, 11, 0):
            e.title, e.description = "Walk: 1:1 with Sam", "Walking call, no screen."
    events.append(Event("g-focus", "Focus block (protected by Chief)", datetime(2026, 10, 20, 14, 0), datetime(2026, 10, 20, 15, 30),
                        "Deep work. Created by Chief after a sleep and meeting audit."))
    events.append(Event("g-train", "Caltrain to Palo Alto", datetime(2026, 10, 20, 18, 20), datetime(2026, 10, 20, 18, 59),
                        "Source: https://www.caltrain.com/schedules/weekday-timetable\nExcerpt: Train 526"))
    events.append(Event("g-mine", "Dentist", datetime(2026, 10, 20, 8, 0), datetime(2026, 10, 20, 9, 0), "my own event"))
    return events


def test_an_untouched_calendar_needs_nothing():
    code, out = run(FakeTools(seeded_events()))
    assert code == 0 and "Nothing to undo" in out


def test_dry_run_lists_the_changes_and_changes_nothing():
    tools = FakeTools(changed_calendar())
    code, out = run(tools)
    assert code == 0 and "DRY RUN" in out and "Would change: 4 event(s)" in out
    assert tools.patched == [] and tools.deleted == []


def test_apply_restores_titles_and_descriptions_and_deletes_only_what_chief_created():
    events = changed_calendar()
    tools = FakeTools(events)
    code, out = run(tools, "--apply", seed.DEMO_FLAG)
    assert code == 0 and "Done: 4 event(s)" in out
    patched = dict(tools.patched)
    assert patched[tuesday(events, "[Async] Vendor sync").id] == {
        "summary": "Vendor sync", "description": "chief-demo-seed key=ev-20261020-14. Fictional demo data, safe to delete.",
    }
    walk = next(e for e in events if e.title == "Walk: 1:1 with Sam")
    assert patched[walk.id]["summary"] == "1:1 with Sam" and "Attendees: sam@example.com." in patched[walk.id]["description"]
    assert sorted(tools.deleted) == ["g-focus", "g-train"]  # the user's own "Dentist" is untouched
    assert "g-mine" not in tools.deleted and "g-mine" not in dict(tools.patched)


def test_a_look_alike_title_on_a_non_seeded_slot_is_left_alone():
    ev = Event("g-x", "[Async] Vendor sync", datetime(2026, 10, 20, 16, 0), datetime(2026, 10, 20, 17, 0), "mine")
    tools = FakeTools(seeded_events() + [ev])
    run(tools, "--apply", seed.DEMO_FLAG)
    assert tools.patched == [] and tools.deleted == []


@pytest.mark.parametrize(
    "argv,env",
    [(["--apply"], DEMO_ENV), (["--apply", seed.DEMO_FLAG], {}), (["--apply", seed.DEMO_FLAG], {"CHIEF_DEMO_GMAIL": "me@gmail.com"})],
)
def test_apply_is_refused_without_the_flag_or_a_demo_looking_account(argv, env):
    tools = FakeTools(changed_calendar())
    code, out = run(tools, *argv, env=env)
    assert code == 2 and "Refusing" in out
    assert tools.patched == [] and tools.deleted == []
