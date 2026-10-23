"""Tell Chert where to send your iMessage replies, and save the webhook secret into .env WITHOUT printing it.

Chert shows a subscription's signing secret exactly once, so this writes it straight to CHERT_WEBHOOK_SECRET in .env
(an existing line is replaced) and prints only whether that worked. It needs CHERT_SIGNING_SECRET in .env.

    python scripts/chert_subscribe.py https://<your-tunnel>.trycloudflare.com/chert/webhook

Run it again after the tunnel address changes (the old subscription keeps pointing at the dead address; delete it in the Chert
console if you like). Then restart the web server so it reads the new secret.
Based on docs.trychert.com/api/webhook-subscriptions (POST /api/v1/webhook-subscriptions {"url", "events"}, HTTPS only);
not yet run against a real account.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ENV = ROOT / ".env"


def write_env(name: str, value: str, path: Path = ENV) -> None:
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.startswith(f"{name}="):
            out.append(f"{name}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{name}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, http: httpx.Client | None = None, env_path: Path = ENV, say=print) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].startswith("https://") or not args[0].endswith("/chert/webhook"):
        say("Usage: python scripts/chert_subscribe.py https://<your-public-address>/chert/webhook  (HTTPS, ending in /chert/webhook)")
        return 2
    if http is None:
        from dotenv import load_dotenv

        load_dotenv(env_path)
    secret = os.getenv("CHERT_SIGNING_SECRET", "").strip()
    if not secret:
        say("CHERT_SIGNING_SECRET is not set in .env (copy it from the Chert console, API keys page).")
        return 2
    base = os.getenv("CHERT_API_BASE") or "https://console.trychert.com/api/v1"
    client = http or httpx.Client(timeout=20)
    try:
        r = client.post(f"{base.rstrip('/')}/webhook-subscriptions", headers={"Authorization": f"Bearer {secret}"},
                        json={"url": args[0], "events": ["message.received"]})
    except httpx.HTTPError as exc:
        say(f"Could not reach Chert ({type(exc).__name__}).")
        return 1
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        say(f"Chert refused it (HTTP {r.status_code}): {data.get('reason') or data.get('error') or data.get('detail') or 'no reason given'}")
        return 1
    found = data.get("secret") or (data.get("subscription") or {}).get("secret") or (data.get("data") or {}).get("secret")
    if not found:
        say("Chert accepted the subscription but sent no secret in the reply. Check the Chert console; set CHERT_WEBHOOK_SECRET by hand.")
        return 1
    write_env("CHERT_WEBHOOK_SECRET", str(found), env_path)
    say(f"Subscribed {args[0]} to message.received. The webhook secret was saved to CHERT_WEBHOOK_SECRET in {env_path.name} (not shown). Restart the web server.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
