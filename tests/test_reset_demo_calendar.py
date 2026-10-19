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


@pytest.fixture(autouse=True)
def _state(tmp_path):
    global STATE
    STATE = tmp_path / "state"  # never the real .chief
    STATE.mkdir()


def run(tools, *argv, env=DEMO_ENV):
    lines: list[str] = []
    code = reset.main(list(argv), tools=tools, write=lines.append, env=env, state_dir=STATE)
    return code, "\n".join(lines)


def remember(*records):
    import json

    (STATE / "memory.json").write_text(json.dumps(list(records)), encoding="utf-8")


def rec(key, skill, decision="accepted"):
    return {"key": key, "skill": skill, "decision": decision, "summary": "x", "at": "2026-10-19T12:00:00"}


def remembered_keys():
    import json

    return [r["key"] for r in json.loads((STATE / "memory.json").read_text("utf-8"))]


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


def test_apply_forgets_the_demo_decisions_so_chief_proposes_the_week_again():
    remember(rec("audit:2026-10-20", "audit"), rec("trip_planner:2026-10-20:palo alto:1800", "trip_planner"),
             rec("draft_reply:drafted:t1", "draft_reply", "drafted"), rec("other:1", "some_user_skill"))
    code, out = run(FakeTools(seeded_events()), "--apply", seed.DEMO_FLAG)
    assert code == 0 and "Nothing to undo" in out and "Forgot 3 remembered decision(s)" in out
    assert remembered_keys() == ["other:1"]  # only the demo skills' decisions go


def test_dry_run_reports_but_keeps_the_decisions():
    remember(rec("audit:2026-10-20", "audit"))
    code, out = run(FakeTools(seeded_events()))
    assert "Would forget 1 remembered decision(s)" in out and "audit:2026-10-20" in out
    assert remembered_keys() == ["audit:2026-10-20"]


def test_no_memory_file_is_fine():
    assert run(FakeTools(seeded_events()), "--apply", seed.DEMO_FLAG)[0] == 0


def test_a_running_server_sees_the_decisions_being_cleared(tmp_path):
    """The web server keeps Memory in process; the reset script edits the file from outside."""
    from chief.memory import Memory

    remember(rec("audit:2026-10-20", "audit"))
    m = Memory(STATE)
    assert m.decided("audit:2026-10-20")
    reset.clear_decisions(STATE)
    assert not m.decided("audit:2026-10-20")  # picked up without a restart
    m.record(key="audit:2026-10-21", skill="audit", decision="accepted", summary="x")
    assert m.decided("audit:2026-10-21") and remembered_keys() == ["audit:2026-10-21"]


class FakeImap:
    def __init__(self, seen=(b"3", b"7")):
        self.seen, self.calls = list(seen), []

    def login(self, user, password):
        self.calls.append(("login", user))

    def select(self, box):
        self.calls.append(("select", box))

    def search(self, *criteria):
        self.calls.append(("search",) + criteria)
        return "OK", [b" ".join(self.seen)]

    def store(self, num, op, flag):
        self.calls.append(("store", num, op, flag))

    def logout(self):
        self.calls.append(("logout",))


APP_ENV = {**DEMO_ENV, "CHIEF_DEMO_APP_PASSWORD": "abcd efgh ijkl mnop"}


def test_apply_sets_the_seeded_emails_back_to_unread_and_only_that():
    imap = FakeImap()
    code, out = run_with_imap(FakeTools(seeded_events()), imap, APP_ENV)
    assert code == 0 and "Seeded emails: 2 set back to unread." in out
    assert ("search", None, "SEEN", "HEADER", "X-Chief-Demo-Seed", "1") in imap.calls  # only seeded, only already-read
    assert [c for c in imap.calls if c[0] == "store"] == [("store", b"3", "-FLAGS", "\\Seen"), ("store", b"7", "-FLAGS", "\\Seen")]
    assert {c[0] for c in imap.calls} == {"login", "select", "search", "store", "logout"}  # never delete, move or write a message
    assert "abcd" not in out and "ijkl" not in out  # the app password is never printed


def test_all_seeded_emails_already_unread():
    code, out = run_with_imap(FakeTools(seeded_events()), FakeImap(seen=()), APP_ENV)
    assert "Seeded emails: all still unread." in out


def test_no_app_password_says_so_instead_of_failing():
    code, out = run_with_imap(FakeTools(seeded_events()), FakeImap(), DEMO_ENV)
    assert code == 0 and "CHIEF_DEMO_APP_PASSWORD is not set" in out


def test_a_mail_failure_does_not_hide_the_calendar_reset():
    class Broken(FakeImap):
        def login(self, user, password):
            raise OSError("no network")

    tools = FakeTools(changed_calendar())
    code, out = run_with_imap(tools, Broken(), APP_ENV)
    assert code == 0 and "Done: 4 event(s)" in out and "Could not mark the seeded emails unread" in out and tools.deleted


def test_dry_run_never_touches_mail():
    imap = FakeImap()
    lines: list[str] = []
    reset.main([], tools=FakeTools(seeded_events()), write=lines.append, env=APP_ENV, state_dir=STATE, imap_factory=lambda: imap)
    assert imap.calls == [] and any("Would also set" in l for l in lines)


def run_with_imap(tools, imap, env):
    lines: list[str] = []
    code = reset.main(["--apply", seed.DEMO_FLAG], tools=tools, write=lines.append, env=env, state_dir=STATE, imap_factory=lambda: imap)
    return code, "\n".join(lines)


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
