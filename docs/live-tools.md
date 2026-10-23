# Live tools (Composio + Parallel)

`CHIEF_MODE=live` uses `chief/tools/live.py` (`LiveTools`). Every method returns the same types as `MockTools`.
First run with real keys on 2026-10-19 against a dedicated demo Google account (see "Verified on 2026-10-19" below). The
parts marked **unverified** have still not been run.

## Setup

```bash
pip install -e ".[live]"          # composio>=0.28 (not composio-core), parallel-web, slack-bolt, ...
```
`.env`: `COMPOSIO_API_KEY` (a key with **write** access to `session_management`), `CHIEF_USER_ID`, `PARALLEL_API_KEY`; optional `CHIEF_TIMEZONE`, `CHIEF_CALENDAR_ID`, `CHIEF_SEARCH_DOMAINS`, `CHIEF_HEALTH_CSV`, `CHIEF_HOME_STATION` (the Caltrain station trips start from, default `San Francisco`), `CHIEF_OWN_DOMAINS`.

Connect the Google test account once (prints a link, open it in a browser signed in to that account):
```bash
python -m chief.tools.composio_client gmail
python -m chief.tools.composio_client calendar
python -m chief.tools.composio_client --status
```
`python -m chief connect gmail` needs this hook in `chief/__main__.py` (not edited by this task):
```python
# before ap.parse_args(...):  if argv and argv[0] == "connect": ...
if (argv if argv is not None else sys.argv[1:])[:1] == ["connect"]:
    from chief.tools.composio_client import connect_main
    return connect_main((argv if argv is not None else sys.argv[1:])[1:])
```

## What was verified, and how

| Item | Status |
|---|---|
| `composio.sessions.create(user_id=, toolkits=[...], tools={toolkit: {"enable": [...]}})`, `session.execute(slug, arguments=)`, `session.authorize(toolkit).redirect_url`, `session.toolkits(toolkits=[..])` | verified in `composio` 0.28.0 source; `execute` returns `.data/.error/.result_type` and never retries |
| `Parallel(api_key).search(objective=, search_queries=, mode="fast", advanced_settings={"max_results", "source_policy": {"include_domains"}})`, result `.results[].title/.url/.excerpts` | verified in `parallel-web` 1.3.5 source and docs.parallel.ai |
| Slugs and top-level argument names below | from the Composio toolkit pages (docs.composio.dev/toolkits/googlecalendar, /gmail) |
| Inner shape of each tool's `data` | verified for `GOOGLECALENDAR_EVENTS_LIST` and `GMAIL_FETCH_EMAILS` on 2026-10-19: the parsers' assumptions held (see below) |
| `Parallel(...).extract(urls=, objective=, advanced_settings={"full_content": {"max_chars_per_result": N}})`, result `.results[].title/.url/.excerpts/.full_content` | verified live against caltrain.com on 2026-10-19 |

## Verified on 2026-10-19 (demo account, real Composio and Parallel)

| Path | Result |
|---|---|
| Connect Gmail and Calendar, `--status` | works; the Composio key needs **write** access to `session_management` (a read-only key gets a 403) |
| `list_events` | 67 real events parsed. Google lists the calendar owner as an attendee of every event (flagged `"self": true`); `_event` drops it. Seeded events carry guests only in the description (`Attendees: a, b.`), which `_event` reads when Google returned no real attendees |
| `fetch_emails`, `fetch_thread` | all 10 seeded emails returned with sender, subject, snippet, time and unread flag |
| `create_event`, `patch_event`, `delete_event` | one approved audit applied three patches and one create; read back from the calendar at the right **local** time (an offset-aware ISO string is accepted, no `CHIEF_TIMEZONE` needed). The delete and the restoring patches were run to put the calendar back |
| `search_web` (Parallel, `include_domains`) | works; results restricted to the pinned domains |
| `extract_page` (Parallel Extract) | works; returns the whole Caltrain timetable page as markdown tables |
| Model call (Cline API) | works with `anthropic/claude-sonnet-4.6` and `moonshotai/kimi-k2.5`, text and tool calls. `deepseek/deepseek-chat` fails (404 upstream) and `minimax/minimax-m2.5` returned empty replies. Cline wraps successful replies as `{"data": {...}}`; `chief/llm.py` unwraps that |
| Latency | cold first read about 4.5 s (Composio session creation), warm Composio reads 0.7 to 2.5 s, Parallel 0.6 to 0.8 s. `CHIEF_LIVE_TIMEOUT` defaults to 10 s for that reason |

**Still unverified:** `create_draft`, `send_email` and `GMAIL_REPLY_TO_THREAD`, Slack, `patch_event` with `start`/`end` changes,
Whisper speech to text, a real phone, the real-window layer.

## Slugs and arguments used

Read session (cannot write or send):

| Chief method | Slug | Arguments sent |
|---|---|---|
| `list_events` | `GOOGLECALENDAR_EVENTS_LIST` | `calendarId` (`primary`), `timeMin`, `timeMax` (offset-aware ISO), `singleEvents=true`, `orderBy=startTime`, `maxResults=250`, `pageToken` (up to 4 pages) |
| `find_free_slots` | none: computed from `list_events` | see note |
| `fetch_emails` | `GMAIL_FETCH_EMAILS` | `query` (Gmail syntax), `max_results`, `verbose=true`, `user_id="me"` |
| `fetch_thread` | `GMAIL_FETCH_MESSAGE_BY_THREAD_ID` | `thread_id`, `user_id="me"` |
| `create_draft` | `GMAIL_CREATE_EMAIL_DRAFT` | `recipient_email`, `subject`, `body`, `thread_id` (if given), `user_id="me"` |

Write session (created only when an approved write runs):

| Chief method | Slug | Arguments sent |
|---|---|---|
| `patch_event` | `GOOGLECALENDAR_PATCH_EVENT` | `event_id`, `calendar_id` (required by the tool), plus `summary`, `description`, `location`, `start_time`, `end_time`, `timezone`. Other `fields` keys raise `ValueError` |
| `create_event` | `GOOGLECALENDAR_CREATE_EVENT` | `calendar_id`, `summary`, `start_datetime`, `end_datetime`, `timezone`*, `description`, `send_updates="none"` |
| `delete_event` | `GOOGLECALENDAR_DELETE_EVENT` | `event_id`, `calendar_id` |
| `send_email` | `GMAIL_SEND_EMAIL` | `recipient_email`, `subject`, `body`, `user_id="me"` |
| `send_email` with `thread_id` | `GMAIL_REPLY_TO_THREAD` | `thread_id`, `recipient_email`, `message_body`, `user_id="me"` |

\*only when `CHIEF_TIMEZONE` is set. `GMAIL_REPLY_TO_THREAD` is not in the original brief; it is used because `GMAIL_SEND_EMAIL` documents no `thread_id`.

`search_web` -> Parallel Search, `mode="fast"`, `advanced_settings.max_results`, and `advanced_settings.source_policy.include_domains` from the `include_domains` argument (LiveTools only; MockTools ignores it) or `CHIEF_SEARCH_DOMAINS`.
**Keenable instead of Parallel.** Set `CHIEF_WEB_PROVIDER=keenable` and `search_web` and `extract_page` go to Keenable's REST API
(`chief/tools/keenable.py`; docs: https://docs.keenable.ai). It works with no key through Keenable's keyless `/public` endpoints
(1,000 requests an hour per IP, shared); a `KEENABLE_API_KEY` (`keen_...`, free 100,000 requests a month) uses `/v1/search` and `/v1/fetch`
and lifts the limit. A `site:caltrain.com` written in a query, or a pinned domain, becomes Keenable's `site` filter. If Keenable errors
(rate limit, no credits, network) and `PARALLEL_API_KEY` is set, Parallel answers instead. Checked live on 2026-10-23: search takes about
0.3 s, and fetching caltrain.com's weekday timetable gives the same 52 San Francisco to Palo Alto trips as Parallel (train 526, 6:20 to 6:59 PM).
Not as good: its boba hits are articles (a Quora question), so the event title omits the shop name and only the link is kept.

`extract_page(url, objective)` -> Parallel Extract with `full_content`, returned as `{"title", "url", "excerpts": [text]}`. Only public `https` URLs with a host and no credentials are accepted. `trip_planner` uses it on caltrain.com's weekday or weekend timetable and `chief/caltrain.py` turns the markdown tables into trips (the parser's rules, such as the ride-time check and the weekday and weekend train series, are in its module docstring). The page text is untrusted data: it is only matched against fixed patterns.
`read_health` reads a CSV (`CHIEF_HEALTH_CSV` or `data/sleep.csv`; format of `chief/health.py`) and **raises if it is missing**, so the audit skill fails loudly in live mode until the file exists. `post_slack` uses `slack_sdk` with `SLACK_BOT_TOKEN` + `SLACK_CHANNEL`.

Decisions worth knowing:
- `find_free_slots` is computed from `list_events` (not `FIND_FREE_SLOTS` / `FREE_BUSY_QUERY`) so the `[Async]` rule matches the mock and no extra response shape is needed.
- `list_events` skips all-day and cancelled events (they are not meetings and would inflate the heavy-day count).
- Times: Google datetimes become naive local; naive input is treated as local (system zone, or `CHIEF_TIMEZONE`).
- Safety: reads/drafts and writes use two separate Composio sessions with separate `tools` enable lists. Whether a write may run is still decided by `ApprovalGate`; `ScopedTools` hides write methods from skills.

## First-run checklist (done on 2026-10-19 except items 4 and 6)

1. `python -m chief.tools.composio_client --status`, then connect both toolkits. (Each call takes about 10 s.)
2. `LiveTools(cfg).list_events(...)` returns your events. If it returns `[]` for a busy calendar, the `data` nesting differs: print `session.execute("GOOGLECALENDAR_EVENTS_LIST", arguments={...}).data` and adjust `_find_list` keys in `live.py`.
3. `fetch_emails()` returns mail with sender, subject, snippet and time. The field names (`messageId`, `threadId`, `sender`, `subject`, `messageText`, `messageTimestamp`, `labelIds`) come from memory of the Composio output, not the docs.
4. `create_draft(...)` returns a non-null `draft_id`; check the draft in Gmail.
5. Approve one `create_event` and one `patch_event` in the test account. Check the event lands at the right local time: if Composio rejects the offset in `start_datetime`/`start_time` (e.g. `-07:00`), set `CHIEF_TIMEZONE` so a naive string plus `timezone` is the safer path (change `_when` in `live.py`). Done: the offset-aware string was accepted and the times were right.
6. `patch_event` accepts `start_time`/`end_time` (argument names from the docs, behaviour still untested; only `summary` and `description` patches have been run).
7. `search_web` with `include_domains=["caltrain.com"]`: confirm results are restricted and have excerpts.
8. On Windows, `CHIEF_TIMEZONE` needs the `tzdata` package (included in the `live` extra).
