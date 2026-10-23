# Handoff: Chief (read this first)

Written 2026-10-19 for the next agent. It has no access to the conversation that built this, so everything you need is here or linked.
Where this file and the code disagree, trust the code and fix this file.

## 1. Mission and deadline

Chief is an open-source personal agent built for the **Cline x Composio x Parallel hackathon** (Open Source AI Week, San Francisco,
Monday 2026-10-19, 11:00 AM to 4:30 PM PDT; submissions due 3:15 PM, demos and judging 3:35 PM). The one hard rule is that the project is
open source and shared. Pre-built code is allowed (the owner confirmed with the hosts). Judges are from Cline, Composio and Parallel.

Chief notices things across the user's calendar, email and sleep data, proposes a fix, and **acts only after an explicit approval**.
The user talks to it by voice from a phone, and everything it does appears on a big "Stage" screen, in the style of the GPT-6 Astra
launch video (one person, one chair, tasks stacking, visible work). The target is a live ~2 minute stage demo plus a backup video.

The owner's priorities: a demo that works live, honesty about what is real vs mock, and no personal data on a projector.

## 2. Honest status (what is verified and what is not)

**Verified against real services (2026-10-19, a dedicated demo Google account (address kept out of this public repo; it is `CHIEF_DEMO_GMAIL` in `.env`), Windows, Python 3.13):**
- **Composio Gmail and Calendar:** connected as the demo account; 10 seeded emails and 67 events read and parsed. One approved audit ran
  3 `patch_event` and 1 `create_event`; the calendar was read back, the times were right, and the day was then reverted by hand
  (restoring patches plus one `delete_event`). The first live run needed two parser fixes in `LiveTools._event` (the calendar owner is
  listed as an attendee of every event, and seeded guests exist only in the description).
- **Parallel:** `search_web` and the new `extract_page` (Parallel Extract, whole page) work. `trip_planner` read a real train from
  caltrain.com's weekday timetable through `chief/caltrain.py` (a validated markdown-table parser).
- **Model:** the Cline API works with `anthropic/claude-sonnet-4.6` (the one in `.env`) and `moonshotai/kimi-k2.5`, text and tool calls.
  `deepseek/deepseek-chat` fails upstream and `minimax/minimax-m2.5` returned empty replies: **model IDs in older docs are stale**, use
  `https://api.cline.bot/api/v1/models`. Cline wraps replies as `{"data": ...}`; `chief/llm.py` unwraps it (before that fix Chief silently
  used canned text with every model).
- **Skills run live:** `audit`, `email_brief` (flags the injection email), `meeting_prep` (Dana, from real threads), `trip_planner`, and
  `draft_reply` as far as the **draft** (a real Gmail draft is saved in the demo account; the send was never approved).
- **Approvals by voice phrasing:** "That sounds good. Go ahead." and "Perfect. Add it." now approve (a short praise opener is stripped). Before
  that fix they fell through to the open-ended model, which invented a confident reply; a muddled yes with something pending now asks again
  instead of letting the model answer. Real approvals made 4 and then 1 calendar changes; `scripts/reset_demo_calendar.py` undid them.
- **Demo cache recorded** (`data/demo_cache.json`, 15 reads, git-ignored) on the demo clock. `demo-offline` plays the whole story from it and
  `demo` mode with a deliberately broken Composio key still answered reads from it.
- **Phone path:** `cloudflared` installed, quick tunnel up, auth (401 without the token), page, `/stt` (Groq Whisper, exact transcript in 0.6 s),
  `/ask` and `/stage` all work through the public https address. **Real-window layer:** `show-login` and `show-demo` worked and Gmail opened
  on the right half of the screen from a live `/ask`.
- `python -m chief doctor`: 0 failing.
- 688 tests pass, 1 skipped (`.venv\Scripts\python.exe -m pytest -q`). All use fakes; no network.
- The full story runs through the intent layer on seeded mock data and was driven through the real web routes with the Stage page
  open in a browser: inbox, calendar changes with NEW/UPDATED badges, research panel, parallel task lanes, approval cards.
- A manual safety scan found no `shell=True`, `eval`/`exec`, `innerHTML`, or secret printing; subprocess calls take argument lists only.

**NOT verified yet:**
- `send_email` and `GMAIL_REPLY_TO_THREAD` (an approved send), `patch_event` with changed start/end times.
- Slack, both the post-only mode and Socket Mode buttons: only fakes have been used, no real workspace.
- **A real phone**: the microphone permission, hold-to-talk recording format and spoken replies on Android Chrome were not confirmed.
- Whether Google Calendar refreshes on screen after an approved write without `CHIEF_SHOW_DEBUG_PORT` (rehearse; set it to 9222 if stale).
- `demo`-mode failover with the network genuinely cut (only simulated with a bad key); approved writes with live down fail with an error by design.
- Tool calling on an *open* model (Claude and Kimi worked). The scripted demo does not need a model; only the `general` skill and phrasing do.
- The `general` skill on the live model, and the Stage with live data.
- Git: the first commit (`59730c1`, 2026-10-19) is the first time any of this was under version control; everything before it was built in a
  plain folder.

## 3. Run it (no accounts needed)

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"     # add ".[live]" for real integrations
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m chief run --once --approve   # one heartbeat on seeded data
.venv\Scripts\python.exe -m chief web                    # voice page + Stage; prints URLs with a token
```
Useful for repeatable runs: `CHIEF_NOW=2026-10-21T08:00:00`, `CHIEF_STATE_DIR=<temp dir>`, `CHIEF_WEB_TOKEN=demo123`.
The Stage is at `/stage?token=...`; the phone page is `/?token=...`.

### Demo day run (live, with the safety net)

The seeded mail is dated Monday 2026-10-19 and the calendar week is anchored to it, so the story only reads right on a **pinned demo clock**
(Tuesday 2026-10-20 08:00: Dana's mail and the invoice are still inside the 48-hour window, the audit finds Tuesday, Dana's call is within 36 h).
Say on stage that it is a demo account with seeded data on a demo clock.

```powershell
# terminal 1: the server (demo = live first, recorded cache if a read fails; windows on the right half of the screen)
$env:CHIEF_NOW = '2026-10-20T08:00:00'; $env:CHIEF_MODE = 'demo'; $env:CHIEF_SHOW_WINDOWS = '1'; .venv\Scripts\python.exe -m chief web --port 8765
# terminal 2: the https tunnel for the phone (the address changes on every start)
& 'C:\Program Files (x86)\cloudflared\cloudflared.exe' tunnel --url http://localhost:8765
```
Phone: `https://<tunnel>.trycloudflare.com/?token=<CHIEF_WEB_TOKEN>`. Stage: `http://localhost:8765/stage?token=...` (F11, left half).
Window layout is `CHIEF_SHOW_WINDOW_POS=720,0` / `CHIEF_SHOW_WINDOW_SIZE=720x900` in `.env` for a 1440x900 screen (`python -m chief show-layout`).
Rebuild the cache on the same clock after any change to the demo data: `$env:CHIEF_NOW = '2026-10-20T08:00:00'; .venv\Scripts\python.exe -m chief record-demo`.

**Say these** (each contains a trigger or an approval word; a model never approves): "What's in my inbox?", "Reply to Sam and say Friday works",
"How's my week looking?", "That sounds good, go ahead", "Prep me for my meeting with Dana", "I'm heading to Palo Alto after this, find me a Caltrain
after 6 and a boba place on the way", "Perfect, add it".

**Reset between rehearsals.** A rehearsal leaves three kinds of state behind, and each one makes the next run go wrong:
1. **Real calendar changes** (`[Async]`, `Walk:`, a focus block, a `Caltrain to Palo Alto + boba at ...` event).
2. **Remembered decisions** in `.chief/memory.json`. Chief never re-proposes something it remembers approving or skipping, so without this the
   second "how's my week looking?" answers "nothing worth your attention" even on a clean calendar.
3. **Read flags** on the seeded emails. Chief only briefs unread mail, and a rehearsal marks Sam's and Maya's read, so the inbox brief quietly
   drops them (3 to answer becomes 1).

One command undoes all three (dry run first by leaving off the flags):
```powershell
.venv\Scripts\python.exe scripts\reset_demo_calendar.py --apply --i-am-using-a-demo-account
```
It only touches what the demo changed (it finds the emails by the seed's `X-Chief-Demo-Seed` header; IMAP only clears the `\Seen` flag) and
refuses without the demo flag and a demo-looking `CHIEF_DEMO_GMAIL`. The running server notices the cleared memory by itself. It cannot remove
the Gmail drafts that "Reply to Sam" saves (Chief has no delete-draft tool): delete extras by hand. To re-seed from scratch use
`scripts/seed_demo_account.py` (it skips what exists).

**On the phone page:** a red **Stop** button appears while Chief is working or speaking. It stops the voice at once, stops waiting, and tells the
server (`POST /stop`) to drop that request's answer and anything it proposed, without remembering a decision. It cannot interrupt a skill
mid-read, and it never touches an approval. Spoken replies are a short clean version (`chief/speech.py`: no tables, symbols or link text,
a long brief becomes its title plus "The details are on the screen"); the full text stays on screen.

**Vision, the `see` skill (`chief/skills/see.py`, route `POST /see`, button "Show Chief a photo" on the phone page):** a flyer, whiteboard, ticket
or invoice photo is read by the model (`LLM.vision`, an image data URL through the same Cline API model) into fixed JSON fields; plain code then
validates them (real date within a sane window, 24-hour time, short plain text with no links, at most 3 events), warns about overlaps with
existing meetings, and builds the only action it can ever propose: `create_event`, through the approval gate. The picture is untrusted: an event
whose own text reads like an instruction to an assistant is dropped, any such text is reported ("I ignored it"), and the model never chooses a tool.
The page shrinks the photo (1600 px, JPEG) before upload; the server checks the real file signature (JPEG, PNG, WebP, GIF) and an 8 MB limit.
Verified 2026-10-23 with the real model on two generated pictures (a normal flyer: right event; a flyer with a hijack message: only the real
event, hijack flagged), and by tests with fakes. **Not yet tried with a real phone camera photo.** Depends on a vision-capable `CHIEF_LLM_MODEL`
(`anthropic/claude-sonnet-4.6` and `moonshotai/kimi-k2.5` both read images through the Cline API).

**Keenable (optional web provider):** `CHIEF_WEB_PROVIDER=keenable` swaps Parallel for Keenable for `search_web` and `extract_page`, keyless or with
`KEENABLE_API_KEY`, falling back to Parallel if it is configured (`chief/tools/keenable.py`, `docs/live-tools.md`). Verified live keyless on
2026-10-23: the trip step found the same train from Keenable's copy of the timetable. Default stays Parallel because its boba results are
real places.

**Chert iMessage (optional, `chief/chert.py`, `docs/chert-setup.md`):** with `CHERT_SIGNING_SECRET` and `CHIEF_NOTIFY_PHONE` set, proposals are
texted to you, and with `CHERT_WEBHOOK_SECRET` (written by `scripts/chert_subscribe.py`) a signed reply from your number is handled like
something you said on the phone page (plain-string approval, never a model; only your number is obeyed; `/chert/webhook` is the one route
outside the phone token). Built from the public docs and tested with fakes only: **never run against a real Chert account.**

**Chert FaceTime is not integrated.** Its docs (docs.trychert.com, read 2026-10-23) say the FaceTime API is in a controlled
private preview. An outbound call is `POST https://facetime.trychert.com/api/v1/calls` with a `ftk_` project key, the project id and an
outbound-enabled line (from onboarding or a sales call), it is audio only, and Chert expects you to run your own LiveKit agent (your own
speech to text, model and text to speech). No transcript API is documented, so a spoken "yes" on a call could not be matched by Chief's plain-string
approval without running our own speech to text on the call audio. Outbound calling also cannot be tried until Chert enables a line. The iMessage
side was not read in detail.

**Slack (optional):** with `SLACK_BOT_TOKEN` (`xoxb-`, scope `chat:write`) and `SLACK_CHANNEL` in `.env`, the web server (and `chief demo`) also
posts what you said, each result or proposal with its details and sources, and the approve, skip or stop outcome to that channel
(`chief/slack_post.py`, post-only, no buttons; approving stays on the phone). Off when either variable is missing; a Slack failure is
logged and dropped. Tested with a fake client only: **never posted to a real workspace.** Setup: `docs/slack-setup.md`.

**Real windows for the trip:** the Caltrain timetable page opens when Chief reads it, then Google Maps transit directions between the two
Caltrain stations and the boba shop open when the trip proposal appears (`chief/screen.py`). Cached or simulated results open nothing.

CLI subcommands: `run`, `web`, `skills`, `reset`, `doctor`, `demo`, `record-demo`, `show-login`, `show-demo`, `show-layout`, `slack`.
`python -m chief doctor` prints what is missing (env var names only, never values). `python -m chief demo` is the one-command stage launcher.

Modes (`CHIEF_MODE`): `mock` (seeded data), `live` (real services), `demo` (live, falling back to the recorded cache on failure,
tagged "cached"), `demo-offline` (cached reads, simulated writes tagged "simulated").

## 4. Architecture in one screen

```
speech/typing -> chief/intent.py -> skill(s) -> Proposal -> ApprovalGate -> (approval) -> tools -> Calendar / Gmail / web
                      |                |                                          ^
                conversation.py   ScopedTools (reads only)              RecordingTools -> ActivityBus -> Stage + real windows
heartbeat (chief/heartbeat.py) ticks skills that stay silent unless worth saying (NO_REPLY default)
```
Diagram: `docs/chief-architecture.excalidraw` (regenerate with `scripts/make_diagram.py`).

| Area | Files |
|---|---|
| Core | `chief/runtime.py` (wiring), `gate.py` (approval gate), `memory.py`, `activity.py` (event bus), `heartbeat.py`, `config.py` |
| Routing | `chief/intent.py` (approve/skip words, follow-ups, parallel split, skill triggers), `conversation.py` |
| Skills (one file each) | `audit`, `email_brief`, `draft_reply`, `meeting_prep`, `open_brief`, `trip_planner`, `files`, `general` in `chief/skills/` (contract: `skills/base.py`; guide: `docs/writing-skills.md`) |
| Tools | `tools/base.py` (contract and tool classes), `mock.py`, `live.py` + `composio_client.py`, `recording.py`, `replay.py` (cache and failover), `local.py` (folders/files); `chief/caltrain.py` reads the Caltrain timetable page that `extract_page` returns |
| Surfaces | `web/app.py` + `index.html` (phone) + `stage.html` (projector), `slack_app.py`, `screen.py` (real browser windows) |
| Demo ops | `launcher.py`, `doctor.py`, `scripts/seed_demo_account.py`, `scripts/tunnel.ps1`, `data/`, `demo/` |
| Docs | `docs/*.md`: `demo-script.md`, `demo-day.md`, `events.md`, `live-tools.md`, `phone-setup.md`, `screen-setup.md`, `slack-setup.md`, `demo-account.md`, `local-actions.md`, `general-assistant.md`, `submission.md`, `video-shotlist.md` |
| Background | `research/chief-research.md` (how Muse, Grok, Dot, OpenClaw, GAIA work; what we copied) |

## 5. Invariants: do not break these

1. **Writes only through `ApprovalGate`.** Skills get `ScopedTools` (read tools only). `WRITE_TOOLS` (`patch_event`, `create_event`,
   `delete_event`, `send_email`) run only after approval. `SAFE_WRITE_TOOLS` (`create_draft`, `post_slack`, `open_folder`, `open_file`) are
   UI-only or harmless. The gate also rejects a skill proposing a tool it did not declare in `propose_tools`.
2. **Approve and skip are plain string matching** in `intent.py` (with filler words and a short praise opener stripped, e.g. "Chief, do it
   please", "That sounds good, go ahead"). A model never decides to approve anything, and with a proposal waiting, a yes-like phrase that does
   not match cleanly gets a "say do it or skip" reply, never an answer from the open-ended model.
3. **Email and web text are untrusted.** `draft_reply` builds recipients and subjects from headers and the user's own words, never model
   output; bodies with links or addresses are discarded. The seeded inbox includes an injection email that must stay "flagged, not followed".
4. **Read-only results are never queued** for approval (`gate.submit`), so "do it" cannot land on an empty card.
5. **Honesty about mock/cached/simulated.** Mock mode must never claim something really happened (see `open_brief`/`files`:
   "Mock mode: nothing was really opened"). Cached and simulated results carry flags the Stage shows.
6. **Local actions are allow-listed.** Default allowed folder is only `demo/` (plus `.chief/briefs`). Desktop/Documents/Downloads are opt-in
   through `CHIEF_LOCAL_ROOTS` because real file names would appear on a projector. Executables and scripts are never opened.
7. **The real-window layer only opens or reloads URLs.** It never clicks, types or changes anything in Gmail or Calendar, and it uses a
   dedicated browser profile, never the user's.
8. **Secrets:** never print, log, or commit them. `.env` is git-ignored. Do not ask the user to paste keys into chat.
9. **The model client (`chief/llm.py`) sends `stream=False` and a 20 s timeout.** Cline's API streams by default; without this every
   call silently falls back to canned text.

## 6. What the owner has to do (an agent cannot)

Creating accounts and entering credentials is the owner's job. **Items 1 to 3 are done** (as of 2026-10-19: a dedicated demo
account, Composio key with write access to `session_management` and both connections, Parallel, Groq and Cline keys in
`.env`); **item 4 is not**. `docs/demo-account.md` and the step list below cover it:
1. A **dedicated demo Google account** (address containing `demo`), 2-Step Verification, an app password, IMAP enabled. Values go in `.env`
   (`CHIEF_DEMO_GMAIL`, `CHIEF_DEMO_APP_PASSWORD`). Never use a personal account on stage.
2. **Composio** account and key (`COMPOSIO_API_KEY`, `CHIEF_USER_ID`), then connect Gmail and Calendar *as the demo account*:
   `python -m chief.tools.composio_client gmail|calendar|--status`.
3. **Parallel** key (`PARALLEL_API_KEY`), **Groq** key for Whisper (`CHIEF_STT_API_KEY`), and a model key. The owner plans to use the
   **Cline API**: `CHIEF_LLM_BASE_URL=https://api.cline.bot/api/v1`, a `provider/model` ID that the live list accepts (currently
   `anthropic/claude-sonnet-4.6`; `deepseek/deepseek-chat` no longer works), key from app.cline.bot.
4. `winget install --id Cloudflare.cloudflared` (the phone mic needs HTTPS), then `python -m chief show-login` to sign the demo account
   into Chief's separate browser profile.

## 7. Recommended next steps, in order

Done on 2026-10-19: install and `doctor`, the first real Composio run (parsers fixed), seeding the demo account, an approved calendar write,
live `audit`, `meeting_prep` and `trip_planner`, and a working model. What is left:

1. Test a **real phone over the tunnel**: mic permission, hold-to-talk, spoken replies. Chrome on Android lists browser speech recognition as
   "partial"; the server-side Whisper path (verified) is the primary one.
2. Rehearse the "Demo day run" above end to end by voice, with the Stage on the left and the real windows on the right, resetting between runs.
   Check that Calendar refreshes after an approval; if not, set `CHIEF_SHOW_DEBUG_PORT=9222`.
3. Record the backup video (`docs/video-shotlist.md`).
4. Try an approved `send_email` from `draft_reply` on the demo account (the draft step works; the send never ran).
5. Optional: benchmark tool calling on a cheaper or open model before trusting the `general` skill live; a real Cline agent/CLI touchpoint
   (today only the Cline *API* is used, as the model host); an undo command for approved calendar changes.
6. Keep `README.md` "Status and verification" honest as things get verified.

Do not run the story on the real clock on a Monday: the meeting-prep and trip steps look at the next 36 hours and the audit looks at the next
heavy day, so they would pick other meetings. Use the pinned demo clock above.

Known small issues: the model sometimes restates facts loosely (a `meeting_prep` Background bullet said a questionnaire was "submitted" when
Dana had sent it); the Stage calendar header labels can overlap at narrow widths; the `/ask` JSON response does not include the per-task
list (the Stage gets task lanes from activity events instead); Slack is untested and optional; the DevTools window-reload path in `screen.py` is
unverified with a real browser.

## 8. Gotchas for working here

- **Windows:** very long paths break some tools (PowerShell `Test-Path` returned false for a path over 260 characters). Use short temp paths.
- Tests must stay green and offline. Never add a test that opens a real file, window, or network connection.
- Several people (or agents) may edit at once: re-read a file right before editing it and keep edits small.
- The mock tools are deterministic relative to `now` (heavy meetings Mon/Tue/Thu); tests pin `NOW = 2026-10-21 08:00` (a Wednesday).
- Test fixtures contain deliberately fake key-like strings (`tests/test_doctor.py`, `tests/test_replay.py`) that verify redaction. They are not real.
- **Where the code lives:** `https://github.com/SriVarshini09/chief` (public, remote `origin`). The first commit was authored as
  `premxai` (GitHub no-reply address) because the owner of the project is `premxai`, who plans to fork this repo. A second remote named
  `premxai` points at `https://github.com/premxai/chief.git` (empty when last checked); this machine's GitHub login is
  `SriVarshini09`, which can push to `origin` but only read `premxai/chief`.
- Git: use the repo-local identity (`git config user.name`, `user.email`), not the global one. Run `gh auth status` before pushing, and never
  force-push. Do not push to `premxai` unless the owner has signed in to `gh` as that account.

## 9. If you only have 20 minutes

Run the tests, run `python -m chief web`, open `/stage`, type "how's my week looking?", then "do it", then "what's in my inbox?". Read
`docs/demo-script.md`. Then do steps 1 to 3 of section 7: the demo cache and the real phone are where the remaining risk is.
