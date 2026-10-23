# iMessage with Chert (optional)

Chief texts you what it wants to do, and your reply goes through the same approval as the phone page.

> Chief texts: "Tuesday has 6 meetings. I can mark 2 as async, make one a walking call and protect a 90-minute focus block. Reply YES to do it, or NO to skip."
> You reply: "go ahead" (any approval phrase that works on the phone page). Chief texts back: "Done. 4 change(s) made."

**Status: written from Chert's public docs (docs.trychert.com/api, read 2026-10-23) and unit-tested with fakes. Not yet run against a real
Chert account.** The FaceTime side is not built: see "Why not FaceTime" below.

## Setup (about 10 minutes, you do this; I cannot sign in for you)

1. Sign in at the Chert console (https://console.trychert.com, or the link Chert gave you) and open the project that has your trial credit.
2. **API keys page:** copy the **signing secret**. **Phone numbers page:** note the line Chert will send from (it must be assigned to your project).
3. Add to `.env` (never commit it):
   ```
   CHERT_SIGNING_SECRET=<the signing secret>
   CHIEF_NOTIFY_PHONE=+14155551234        # YOUR phone, the one that gets the texts and is allowed to reply
   ```
   Restart `python -m chief web`. It prints `Texting proposals to +14***34 through Chert iMessage (send only...)`.
4. To accept your **replies**, Chert must call Chief, so Chief needs a public HTTPS address (the Cloudflare tunnel). With the tunnel running:
   ```
   python scripts/chert_subscribe.py https://<your-tunnel>.trycloudflare.com/chert/webhook
   ```
   This registers the `message.received` webhook and saves the webhook secret to `CHERT_WEBHOOK_SECRET` in `.env` **without printing it**
   (Chert shows it only once). Restart the web server. Do this again whenever the tunnel address changes.

## What it does and does not do

- A proposal that needs a yes (calendar changes, a send) is texted to you. Read-only answers are not (they are for the screen).
- Your reply is handled exactly like something you said on the phone page: approval is plain string matching on your words, never a model,
  and nothing is written without it. Anything else you text ("what's in my inbox?") is answered with the short spoken version.
- **Only your number is obeyed** (`CHIEF_NOTIFY_PHONE`, compared by its last ten digits). A text from anyone else is ignored with no reply.
- **Only signed webhooks are accepted**: HMAC-SHA256 of `<timestamp>.<body>` with your webhook secret, a 5 minute window, each event id once.
  The route `/chert/webhook` is the one route outside the phone token, because Chert cannot send one.
- A Chert outage never affects Chief: texts go out on a background thread and errors are logged and dropped.
- Off unless `CHERT_SIGNING_SECRET` and `CHIEF_NOTIFY_PHONE` are both set; inbound also needs `CHERT_WEBHOOK_SECRET`.

## Why not FaceTime

Chert's FaceTime API is in a "controlled private preview" (their words). An outbound call needs an **outbound-enabled line** that Chert
provisions, is audio only, and expects **your own LiveKit voice agent** (your own speech to text, model and text to speech); Chert's hosted
assistants are documented for inbound calls only, configured in the dashboard, with no API. No call transcript is documented, so a spoken
"yes" could not be matched the way Chief requires without running our own speech to text on the call audio. If Chert enables an outbound
line for you and you have a LiveKit project, a FaceTime channel is possible; it is a separate build.

## Check it

```
python -m pytest tests/test_chert.py -q
```
First live test once the secret and your number are in `.env`: ask "how's my week looking?" on the phone page and your phone should get the text.
