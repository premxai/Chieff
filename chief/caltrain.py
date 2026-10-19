"""Reads a Caltrain timetable page (caltrain.com/schedules/weekday-timetable) into trips between two stations.

The page text comes back from `extract_page` as markdown tables, one per direction: a header row of train numbers, then
one row per station with the time each train is there, and `--` where it does not stop. A trip is a train column in
which BOTH the origin row and the destination row have a time. Nothing is guessed, and a table that does not look right
is thrown away whole:
  * every station row must have exactly one cell per train number;
  * a column is used only if the ride lasts between MIN_RIDE and MAX_RIDE minutes (a wrong-direction or mis-parsed
    column never does);
  * only trains of the right day type count (weekday service is 1XX / 4XX / 5XX, weekend service is 6XX).
The text is untrusted web data: it is only ever matched against fixed patterns, never followed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

MIN_RIDE = 5  # minutes
MAX_RIDE = 180
WEEKDAY_SERIES = ("1", "4", "5")
WEEKEND_SERIES = ("6",)

_TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap])m$", re.I)
_LINK = re.compile(r"^\[([^\]]+)\]\([^)]*\)$")


@dataclass(frozen=True)
class Trip:
    number: str
    origin: str
    dest: str
    depart: datetime
    arrive: datetime


@dataclass
class Table:
    trains: list[str]
    rows: dict[str, list[time | None]]  # lower-cased station name -> one time (or None) per train


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clock(cell: str) -> time | None:
    m = _TIME.match(cell.replace("\\", "").strip())
    if not m:
        return None  # "--", "\--", blank, or anything else: the train is not there
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (1 <= hour <= 12 and 0 <= minute < 60):
        return None
    return time((hour % 12) + (12 if m.group(3).lower() == "p" else 0), minute)


def _station(cell: str) -> str:
    m = _LINK.match(cell)
    return (m.group(1) if m else cell).strip().lower()


def parse_tables(text: str) -> list[Table]:
    """Every well-formed timetable table in the page, in page order. A malformed table is skipped entirely."""
    tables: list[Table] = []
    block: list[str] = []
    for line in text.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            block.append(line)
            continue
        if block:
            table = _table(block)
            if table:
                tables.append(table)
            block = []
    return tables


def _table(lines: list[str]) -> Table | None:
    header = _cells(lines[0])
    if len(header) < 3 or header[1].lower() != "station name":
        return None
    trains = header[2:]
    if not all(re.fullmatch(r"\d{3}", t) for t in trains):
        return None
    rows: dict[str, list[time | None]] = {}
    for line in lines[1:]:
        cells = _cells(line)
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c):  # the | --- | --- | separator
            continue
        name = _station(cells[1]) if len(cells) > 1 else ""
        if not name:
            continue  # a spacer row
        times = cells[2:]
        if len(times) != len(trains):
            return None  # misaligned row: do not trust any of this table
        rows[name] = [_clock(c) for c in times]
    return Table(trains, rows) if rows else None


def _chronological(day: date, times: list[time | None]) -> list[datetime | None]:
    """Clock times to datetimes on `day`; a time that jumps back by more than 6 hours is past midnight."""
    out: list[datetime | None] = []
    offset, prev = 0, None
    for t in times:
        if t is None:
            out.append(None)
            continue
        if prev is not None and (prev.hour * 60 + prev.minute) - (t.hour * 60 + t.minute) > 360:
            offset += 1
        prev = t
        out.append(datetime.combine(day + timedelta(days=offset), t))
    return out


def trips(text: str, origin: str, dest: str, day: date, weekend: bool = False) -> list[Trip]:
    """Trains that run origin -> dest on `day`, earliest first. Empty if either station is missing or nothing validates."""
    o, d = origin.strip().lower(), dest.strip().lower()
    series = WEEKEND_SERIES if weekend else WEEKDAY_SERIES
    found: dict[tuple[str, datetime], Trip] = {}
    for table in parse_tables(text):
        if o not in table.rows or d not in table.rows:
            continue
        deps, arrs = _chronological(day, table.rows[o]), _chronological(day, table.rows[d])
        for number, dep, arr in zip(table.trains, deps, arrs):
            if dep is None or arr is None or not number.startswith(series):
                continue
            if arr < dep:  # arrived after midnight
                arr += timedelta(days=1)
            if not (MIN_RIDE <= (arr - dep).total_seconds() / 60 <= MAX_RIDE):
                continue
            found[(number, dep)] = Trip(number, origin.strip(), dest.strip(), dep, arr)
    return sorted(found.values(), key=lambda t: t.depart)
