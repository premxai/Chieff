"""What Chief says out loud, as opposed to what it shows on screen.

The on-screen text can be a markdown brief with tables, headings, bullets and long links. A speech engine reads every
symbol of that ("pipe dash dash dash pipe ..."), so the phone page speaks the output of `spoken()` instead: plain
sentences, no table or markup characters, links reduced to their site name, and a hard length limit with a pointer to
the screen for the rest. Pure string handling: nothing here calls a model.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

MAX_SPOKEN = 380  # characters: about 25 seconds of speech
ON_SCREEN = "The details are on the screen."

_URL = re.compile(r"https?://[^\s)\]>]+", re.I)
_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://)[^)]*\)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|?\s*$")
_RULE = re.compile(r"^\s*(?:[-*_=]{3,}|\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?)\s*$")


def _site(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host or "the link"


def _is_document(text: str) -> bool:
    """A brief or other markdown page: headings, tables or a horizontal rule, not a plain reply."""
    lines = text.splitlines()
    return any(l.lstrip().startswith("#") or _TABLE_ROW.match(l) and "|" in l for l in lines) or any(_RULE.match(l) for l in lines)


def _plain(text: str) -> str:
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub(lambda m: _site(m.group(0)), text)
    text = re.sub(r"[`*_~>#]+", "", text)  # emphasis, code, quote and heading marks
    text = re.sub(r"\s*[—–]\s*", ", ", text)  # dashes in prose are a pause, not the word "dash"
    text = re.sub(r"\s+-\s+", ", ", text)
    text = re.sub(r"[|\\]+", " ", text)
    text = re.sub(r"\(\s*\)", "", text)  # parentheses emptied by removing a link
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)  # emoji
    text = re.sub(r"\s*\n\s*", ". ", text)
    text = re.sub(r"\.(\s*\.)+", ".", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,;:").lstrip(". ")


def spoken(text: str) -> str:
    """The text to speak for `text`. Short replies come back almost unchanged; a document gets its title and a pointer."""
    text = (text or "").strip()
    if not text:
        return ""
    if _is_document(text):
        title = next((l for l in text.splitlines() if l.strip() and not _RULE.match(l) and not _TABLE_ROW.match(l)), "")
        head = _plain(title).rstrip(".")
        return f"{head}. {ON_SCREEN}" if head else ON_SCREEN
    out = _plain(text)
    if len(out) <= MAX_SPOKEN:
        return out
    cut = out[:MAX_SPOKEN]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    cut = cut[: end + 1] if end > MAX_SPOKEN // 3 else cut.rsplit(" ", 1)[0]
    return f"{cut.rstrip(' ,;:.')}. {ON_SCREEN}"
