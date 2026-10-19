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


def main(argv=None, tools=None, now=None, write=print, env=None) -> int:
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

    write(f"Demo calendar reset: {'APPLY' if args.apply else 'DRY RUN (nothing will change)'}; seeded week anchored to {anchor:%Y-%m-%d}.")
    if not steps:
        write("Nothing to undo: the calendar already matches the seed.")
        return 0
    for action, ev, fields in steps:
        label = f"restore '{fields['summary']}'" if action == "patch" else "delete"
        write(f"  {label:34} <- {ev.start:%a %m-%d %H:%M}  {ev.title}")
        if args.apply:
            tools.patch_event(ev.id, fields) if action == "patch" else tools.delete_event(ev.id)
    write(f"{'Done' if args.apply else 'Would change'}: {len(steps)} event(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
