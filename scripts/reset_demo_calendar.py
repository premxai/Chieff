"""Put the DEMO calendar back the way scripts/seed_demo_account.py made it, so the story can be run again.

An approved demo changes real events in the demo account: the audit renames meetings ("[Async] ...", "Walk: ...") and adds a
"Focus block (protected by Chief)", and the trip step adds a "Caltrain to ..." event. This undoes exactly those, nothing else:

  * a seeded meeting whose title is one of Chief's renamings of the seeded title (or whose description Chief replaced) is
    patched back to the seeded title and description;
  * an event Chief created (the focus block, or a Caltrain event with its source line) is deleted.

It never touches an event it does not recognise. Dry run by default; --apply needs --i-am-using-a-demo-account and a
CHIEF_DEMO_GMAIL address that looks like a demo account, like the seed script. Gmail drafts the demo saved are NOT removed
(Chief has no delete-draft tool): delete them by hand in the demo mailbox if they clutter it.

    python scripts/reset_demo_calendar.py                       # dry run: prints what it would do
    python scripts/reset_demo_calendar.py --apply --i-am-using-a-demo-account
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import seed_demo_account as seed  # noqa: E402

FOCUS_NOTE = "Created by Chief after a sleep and meeting audit"
TRIP_TITLE = "Caltrain to "
TRIP_SOURCE = "Source: https://www.caltrain.com"
DEFAULT_SEED_DAY = "2026-10-19"  # the day the demo account was seeded: the seeded week is anchored to it


def plan_reset(events, seeded):
    """[(action, event, fields)] to undo what the demo changed. `events` are the calendar's, `seeded` the seed script's."""
    by_start = {s.start: s for s in seeded}
    steps = []
    for ev in events:
        desc = getattr(ev, "description", "") or ""
        if FOCUS_NOTE in desc or (ev.title.startswith(TRIP_TITLE) and TRIP_SOURCE in desc):
            steps.append(("delete", ev, None))
            continue
        s = by_start.get(ev.start)
        if s is None or ev.title not in {s.title, f"[Async] {s.title}", f"Walk: {s.title}"}:
            continue
        if ev.title != s.title or seed.MARKER not in desc:
            steps.append(("patch", ev, {"summary": s.title, "description": s.description}))
    return steps


DEMO_SKILLS = {"audit", "trip_planner", "draft_reply", "meeting_prep", "email_brief"}


def mark_seeded_mail_unread(env, imap_factory=None) -> int:
    """Set the seeded emails back to unread. Chief only briefs UNREAD mail, and a rehearsal (or a click in Gmail) marks them
    read, so "what's in my inbox" would quietly lose Sam and Maya. Same IMAP login as the seed script; it only clears the
    \\Seen flag on messages carrying the seed's Message-ID, and never deletes or moves anything (it finds them by the seed's X-Chief-Demo-Seed header). Returns how many changed."""
    import imaplib

    address, password = (env.get(seed.ENV_GMAIL) or "").strip(), (env.get(seed.ENV_PASSWORD) or "").replace(" ", "").strip()
    if not address or not password or not seed.looks_like_demo(address):
        return -1  # not configured: the caller says so
    factory = imap_factory or (lambda: imaplib.IMAP4_SSL(seed.IMAP_HOST))
    conn = factory()
    changed = 0
    try:
        conn.login(address, password)
        conn.select(seed.MAILBOX)
        # The seed stamps every message it adds with this header. (Gmail matches Message-ID only as a whole, not a substring.)
        _, data = conn.search(None, "SEEN", "HEADER", "X-Chief-Demo-Seed", "1")
        for num in (data[0] or b"").split():
            conn.store(num, "-FLAGS", "\\Seen")
            changed += 1
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return changed


def demo_decisions(state_dir: Path) -> list[dict]:
    """The remembered approve/skip/draft decisions of the demo skills in <state_dir>/memory.json."""
    import json

    path = Path(state_dir) / "memory.json"
    if not path.exists():
        return []
    try:
        return [r for r in json.loads(path.read_text("utf-8")) if r.get("skill") in DEMO_SKILLS]
    except (OSError, ValueError):
        return []


def clear_decisions(state_dir: Path) -> int:
    """Forget those decisions. Without this Chief stays silent about a week it already acted on ("nothing worth your
    attention"), because it never re-proposes something it remembers approving or skipping. A running server notices
    the edit by itself (chief/memory.py reloads the file when it changes)."""
    import json

    path = Path(state_dir) / "memory.json"
    if not path.exists():
        return 0
    try:
        records = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return 0
    keep = [r for r in records if r.get("skill") not in DEMO_SKILLS]
    path.write_text(json.dumps(keep, indent=2), encoding="utf-8")
    return len(records) - len(keep)


def main(argv=None, tools=None, now=None, write=print, env=None, state_dir=None, imap_factory=None) -> int:
    ap = argparse.ArgumentParser(description="Put the demo calendar back the way the seed script made it (dry run by default).")
    ap.add_argument("--apply", action="store_true", help="really patch and delete (default: dry run)")
    ap.add_argument(seed.DEMO_FLAG, dest="demo_ok", action="store_true", help="confirm the target is a demo account")
    ap.add_argument("--seed-day", default=DEFAULT_SEED_DAY, help=f"day the seed ran, YYYY-MM-DD (default {DEFAULT_SEED_DAY})")
    args = ap.parse_args(argv)

    if args.apply and tools is None:  # .env is read only when really applying, like the seed script
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
    env = env if env is not None else os.environ
    if args.apply:
        address = (env.get(seed.ENV_GMAIL) or "").strip()
        if not args.demo_ok or not address or not seed.looks_like_demo(address):
            write(f"Refusing to change anything: --apply needs {seed.DEMO_FLAG} and {seed.ENV_GMAIL} set to a demo-looking gmail.com address.")
            return 2

    anchor = datetime.fromisoformat(args.seed_day)
    seeded = seed.build_events(anchor)
    start, end = anchor - timedelta(days=seed.PAST_DAYS), anchor + timedelta(days=seed.FUTURE_DAYS + 1)
    if tools is None:
        from chief.config import Config
        from chief.tools.live import LiveTools

        tools = LiveTools(Config.from_env())
    steps = plan_reset(tools.list_events(start, end), seeded)
    if state_dir is None:
        from chief.config import Config

        state_dir = Config.from_env().state_dir
    remembered = demo_decisions(state_dir)

    write(f"Demo calendar reset: {'APPLY' if args.apply else 'DRY RUN (nothing will change)'}; seeded week anchored to {anchor:%Y-%m-%d}.")
    if not steps:
        write("Nothing to undo: the calendar already matches the seed.")
    for action, ev, fields in steps:
        label = f"restore '{fields['summary']}'" if action == "patch" else "delete"
        write(f"  {label:34} <- {ev.start:%a %m-%d %H:%M}  {ev.title}")
        if args.apply:
            tools.patch_event(ev.id, fields) if action == "patch" else tools.delete_event(ev.id)
    if steps:
        write(f"{'Done' if args.apply else 'Would change'}: {len(steps)} event(s).")
    if remembered:
        cleared = clear_decisions(state_dir) if args.apply else len(remembered)
        write(f"{'Forgot' if args.apply else 'Would forget'} {cleared} remembered decision(s) so Chief proposes them again: "
              + ", ".join(sorted({r['key'] for r in remembered})))
    if args.apply:
        try:
            n = mark_seeded_mail_unread(env, imap_factory)
        except Exception as exc:  # a mail hiccup must not hide that the calendar was reset
            write(f"Could not mark the seeded emails unread ({type(exc).__name__}): set them unread in Gmail by hand.")
        else:
            write("Seeded emails: not checked (CHIEF_DEMO_APP_PASSWORD is not set); mark them unread in Gmail by hand."
                  if n < 0 else f"Seeded emails: {n} set back to unread." if n else "Seeded emails: all still unread.")
    else:
        write("Would also set any seeded email you or a rehearsal marked read back to unread (needs --apply).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
