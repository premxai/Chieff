"""files: "open my desktop", "open the demo folder", "what's in my downloads".

Opens an allow-listed folder on screen and says, in a sentence, what is in it. Strictly read-only: it proposes
nothing, never writes, moves or deletes, and never opens a file (that is open_brief's job, for one saved page).

Name resolution is deterministic, no model involved: the spoken name must equal one of the allow-listed
folder names (chief.tools.local.named_folders), optionally followed by "in/on my <folder>" to pick one
sub-folder by its exact name. Unknown, empty or ambiguous names are refused with a list of what can be opened.
The tools re-check every path against the allow-list, so this module is a convenience, not the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

from chief.models import Proposal
from chief.skills.base import Context, Skill
from chief.tools.local import named_folders

SPOKEN = {  # extra spoken forms for the built-in names; custom roots answer to their own folder name only
    "desktop": ["desktop"],
    "documents": ["documents"],
    "downloads": ["downloads", "download"],
    "demo": ["demo", "demo folder"],
    "briefs": ["briefs"],
}
LIST_WORDS = ("what's in", "whats in", "what is in", "what's on", "whats on", "what is on", "what's inside", "what is inside", "list", "contents of")
OPEN_WORDS = ("open", "show me", "show", "pull up", "bring up", "go to", "look in", "look inside")
FILLER = {"my", "the", "a", "an", "me", "up", "please", "folder", "directory", "dir", "files", "file"}
PREPOSITIONS = (" in ", " on ", " inside ", " under ", " within ")
SHOWN = 5


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("’", "'")).split())


def _clean(name: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", name)[:60]


def _triggers() -> list[str]:
    nouns = ["desktop", "documents", "downloads", "demo folder", "downloads folder", "documents folder", "briefs folder", "briefs"]
    verbs = ["open my", "open the", "show me my", "show me the", "pull up my", "pull up the"]
    ask = ["what's in my", "what's in the", "whats in my", "what is in my", "what is in the", "what's on my", "whats on my", "what is on my"]
    out = [f"{v} {n}" for v in verbs + ask for n in nouns]
    out += ["open a folder", "open my folder", "open the folder", "open folder"]
    # finding and opening one FILE by name: "find my budget on my desktop", "open the file pitch deck"
    out += ["find the file", "find a file", "find my file", "find files", "find file", "look for a file", "look for the file", "locate the file",
            "where is the file", "where is my file", "open the file", "open file", "open my file"]
    for root in ("desktop", "documents", "downloads"):
        out += [f"on my {root}", f"in my {root}", f"search my {root}", f"files on my {root}", f"files in my {root}"]
    return out


def _phrase(said: str) -> tuple[bool, str]:
    """(only_list, the rest) with the leading verb removed. A question like "what's in ..." only lists."""
    for w in sorted(LIST_WORDS + OPEN_WORDS, key=len, reverse=True):
        if said == w or said.startswith(w + " "):
            return w in LIST_WORDS, said[len(w):].strip()
    return False, said


def _split_sub(phrase: str) -> tuple[str, str]:
    """'acme in my documents' -> ('acme', 'my documents'); no preposition -> ('', phrase)."""
    padded = f" {phrase} "
    hits = [(padded.find(p), p) for p in PREPOSITIONS if p in padded]
    if not hits:
        return "", phrase
    pos, p = min(hits)
    return padded[:pos].strip(), padded[pos + len(p):].strip()


def _words(phrase: str) -> str:
    return " ".join(w for w in phrase.split() if w not in FILLER)


def resolve_root(phrase: str, folders: dict[str, list[Path]]) -> tuple[str | None, str]:
    """-> (name, "") on success, or (None, reason) for an empty, unknown or ambiguous name."""
    wanted = _words(phrase)
    if not wanted:
        return None, "Which folder?"
    hits = [n for n in folders if wanted == n or wanted in SPOKEN.get(n, [])]
    if not hits:
        return None, f'I don\'t know a folder called "{wanted}".'
    if len(hits) > 1 or len(folders[hits[0]]) > 1:
        options = [str(p) for n in hits for p in folders[n]]
        return None, f'"{wanted}" could mean more than one folder ({"; ".join(options)}).'
    return hits[0], ""


def _refuse(why: str, folders: dict[str, list[Path]], key: str) -> Proposal:
    return Proposal(skill="files", summary=f"{why} I can open: {', '.join(sorted(folders))}.", actions=[], key=f"files:refused:{key}")


def _sentence(listing: dict, label: str, opened: bool) -> str:
    n = listing["count"]
    if n == 0:
        return f"Opened {label} on your screen. It is empty." if opened else f"{label} is empty."
    names = [_clean(e["name"]) + ("/" if e["kind"] == "folder" else "") for e in listing["entries"][:SHOWN]]
    more = f", and {n - len(names)} more" if n > len(names) else ""
    body = f"{n} item(s) ({listing['folders']} folder(s), {listing['files']} file(s)): {', '.join(names)}{more}."
    return f"Opened {label} on your screen. {body}" if opened else f"{label} holds {body}"


FIND_VERBS = ("find", "search for", "search", "look for", "locate", "where is", "where are")
OPEN_VERBS = ("open", "show me", "show", "pull up", "bring up")
POLITE = ("can you ", "could you ", "please ", "hey chief ", "chief ")
NAME_FILLER = {"my", "the", "a", "an", "file", "files", "called", "named", "for", "me", "please", "document", "documents"}
ROOT_WORDS = ("desktop", "documents", "downloads", "demo", "briefs")
SEARCH_DEPTH, SEARCH_FOLDERS, SEARCH_HITS = 3, 60, 8


def _find_request(said: str) -> tuple[str, str, bool] | None:
    """(what to look for, which folder it was said to be in or "", open it?) when this is a request about a FILE, else None.
    "find my budget on my desktop", "search my documents for the contract", "open the file pitch deck". A bare "open my desktop" is
    a folder request, not this."""
    for p in POLITE:
        if said.startswith(p):
            said = said[len(p):]
    verb = next((v for v in sorted(FIND_VERBS + OPEN_VERBS, key=len, reverse=True) if said == v or said.startswith(v + " ")), None)
    if verb is None:
        return None
    rest = said[len(verb):].strip()
    opening = verb in OPEN_VERBS
    words = rest.split()
    mentions_file = "file" in words or "files" in words
    if opening and not mentions_file:
        return None  # "open my desktop" opens a folder
    m = re.match(rf"^(?:my |the )?({'|'.join(ROOT_WORDS)}) (?:for|to find) (.+)$", rest)  # "search my desktop for budget"
    if m:
        name, root = m.group(2), m.group(1)
    else:
        before, after = _split_sub(rest)  # "budget on my desktop" -> ("budget", "my desktop"); no "on/in/from" -> ("", rest)
        name, root = (before, after) if before else (rest, "")
    mentions_root = bool(root) or any(r in words for r in ROOT_WORDS)
    if not opening and not (mentions_file or mentions_root):
        return None  # "find me a boba spot": not about files
    query = " ".join(w for w in _norm(name).split() if w not in NAME_FILLER)
    return query, root, opening


def _search(ctx: Context, roots: list[Path], query: str) -> list[tuple[Path, str]]:
    """Files whose name contains every word asked for, found by walking the allowed folders through list_folder (which re-checks the
    allow-list on every call): (full path, the folder it is in, shown like 'Documents/Acme')."""
    from collections import deque

    tokens = _norm(query).split()
    hits: list[tuple[Path, str]] = []
    queue = deque((p, p.name, 0) for p in roots)
    visited = 0
    while queue and visited < SEARCH_FOLDERS and len(hits) < SEARCH_HITS:
        path, label, depth = queue.popleft()
        visited += 1
        try:
            entries = ctx.tools.list_folder(str(path))["entries"]
        except Exception:
            continue
        for e in entries:
            n = _norm(e["name"])
            if e["kind"] == "file" and tokens and all(t in n for t in tokens):
                hits.append((path / e["name"], label))
            elif e["kind"] == "folder" and depth < SEARCH_DEPTH:
                queue.append((path / e["name"], f"{label}/{_clean(e['name'])}", depth + 1))
    return hits


def _find(ctx: Context, folders: dict[str, list[Path]], query: str, root_phrase: str, opening: bool) -> Proposal:
    if not query:
        return Proposal(skill="files", summary="Which file? Say its name, for example: find my budget on my desktop.", actions=[], key="files:find:none")
    if root_phrase:
        name, why = resolve_root(root_phrase, folders)
        if name is None:
            return _refuse(why, folders, f"find:{_words(root_phrase) or 'none'}")
        roots, where = [folders[name][0]], name.title() if name != "demo" else "the demo folder"
    else:
        seen: dict[str, Path] = {}
        for paths in folders.values():
            for p in paths:
                seen.setdefault(str(p), p)
        roots, where = list(seen.values()), "the folders I may open"
    hits = _search(ctx, roots, query)
    key = f"files:find:{query}:{where}"
    if not hits:
        return Proposal(skill="files", summary=f'I didn\'t find a file matching "{query}" in {where}.', actions=[], key=key + ":none")
    lines = [f"- {_clean(p.name)} (in {label})" for p, label in hits]
    plural = "files" if len(hits) > 1 else "file"
    found = f'I found {len(hits)} {plural} matching "{query}" in {where}:\n' + "\n".join(lines)
    if not opening:
        tail = ' Say "open the file ..." with more of its name and I\'ll show its folder.' if len(hits) > 1 else f' Say "open the file {_words(_norm(hits[0][0].stem))}" and I\'ll show its folder.'
        return Proposal(skill="files", summary=found + tail, actions=[], key=key)
    if len(hits) > 1:
        return Proposal(skill="files", summary=found + "\nWhich one? Say more of its name.", actions=[], key=key + ":many")
    path, label = hits[0]
    try:
        res = ctx.tools.open_folder(str(path.parent))  # the folder, never the file: this skill only ever opens folders
    except Exception as exc:
        return Proposal(skill="files", summary=f"I found {_clean(path.name)} (in {label}) but couldn't open its folder: {exc}.", actions=[], key=key + ":refused")
    mock = isinstance(res, dict) and bool(res.get("simulated"))
    opened = (f"(Mock mode: nothing was really opened.) {_clean(path.name)} is in {label}." if mock
              else f"{_clean(path.name)} is in {label}; I opened that folder on your screen.")
    return Proposal(skill="files", summary=opened, actions=[], key=key + ":opened")


def run(ctx: Context) -> Proposal | None:
    said = _norm(ctx.request)
    if not said:
        return None
    folders = named_folders(state_dir=ctx.memory.path.parent)
    find = _find_request(said)
    if find is not None:
        return _find(ctx, folders, *find)
    only_list, phrase = _phrase(said)
    sub, root_phrase = _split_sub(phrase)
    if sub and not _words(sub):
        sub = ""  # "the folder in my documents" names no sub-folder
    name, why = resolve_root(root_phrase, folders)
    if name is None:
        return _refuse(why, folders, _words(phrase) or "none")
    target = folders[name][0]
    label = name.title() if name != "demo" else "the demo folder"

    simulated = False
    try:
        if sub:
            wanted = _norm(_words(sub))
            subdirs = [e["name"] for e in ctx.tools.list_folder(str(target))["entries"] if e["kind"] == "folder"]
            kids = [d for d in subdirs if _norm(d) == wanted]
            if len(kids) != 1:
                why = f'"{wanted}" matches {len(kids)} folders in {label}.' if kids else f'There is no folder called "{wanted}" in {label}.'
                return Proposal(
                    skill="files", actions=[], key=f"files:refused:{name}:{wanted}",
                    summary=f"{why} Folders there: {', '.join(_clean(d) for d in subdirs[:8]) or 'none'}.",
                )
            target, label = target / kids[0], f"{_clean(kids[0])} in {label}"
        if not only_list:
            res = ctx.tools.open_folder(str(target))
            simulated = isinstance(res, dict) and bool(res.get("simulated"))
        listing = ctx.tools.list_folder(str(target))
    except Exception as exc:  # LocalActionError, or the demo wrapper's RuntimeError around it: say it, never crash
        verb = "list" if only_list else "open"
        return Proposal(skill="files", summary=f"I couldn't {verb} {label}: {exc}.", actions=[], key=f"files:error:{name}")

    text = _sentence(listing, label, opened=not only_list)
    if simulated:  # mock mode: be honest that nothing actually opened on the screen
        text = text.replace(f"Opened {label} on your screen.", f"(Mock mode: nothing was really opened.) {label}:", 1)
    if only_list:
        text += f' Say "open {_words(root_phrase)}" and I\'ll put it on screen.'
    return Proposal(skill="files", summary=text, actions=[], key=f"files:{name}:{'list' if only_list else 'open'}")


SKILL = Skill(
    name="files",
    description="Open your desktop, documents, downloads or demo folder on screen and say what is in it; find a file by name and show the folder it is in (read-only, allow-listed folders; it never opens a file itself).",
    triggers=_triggers(),
    allowed_tools={"list_folder", "open_folder"},
    propose_tools=set(),
    run=run,
    heartbeat=False,
)
