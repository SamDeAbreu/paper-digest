"""Entry point:  python -m digest [--force] [--dry-run] [--out out/digest.html]"""

from __future__ import annotations

import argparse
import json
import logging
import os
import smtplib
import ssl
import sys
import time
import tomllib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from zoneinfo import ZoneInfo

from . import sources
from .filtering import FollowedAuthor, assign_themes, load_themes, tag_followed
from .render import render
from .sources import Paper

log = logging.getLogger("digest")
ROOT = Path(__file__).resolve().parent.parent
FOLLOWED = "From authors you follow"
FOLLOWED_COLOR = "#b45309"


# ── state ────────────────────────────────────────────────────────────────────

def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"seen": {}, "pending": {}, "last_sent": None}


def save_state(path: Path, st: dict, today: date) -> None:
    keep_seen = (today - timedelta(days=120)).isoformat()
    keep_pending = (today - timedelta(days=14)).isoformat()
    st["seen"] = {k: v for k, v in sorted(st["seen"].items()) if v >= keep_seen}
    st["pending"] = {k: v for k, v in sorted(st["pending"].items()) if v >= keep_pending}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")


# ── collection ───────────────────────────────────────────────────────────────

def collect(cfg: dict, since: date, followed: list[FollowedAuthor], notes: list[str]) -> list[Paper]:
    watch = {i.replace("-", "").upper(): j["name"] for j in cfg["journals"] for i in j["issn"]}
    found: dict[str, Paper] = {}

    def add(ps: list[Paper]) -> None:
        for p in ps:
            if p.key in found:
                continue
            found[p.key] = p

    failed = []
    for j in cfg["journals"]:
        try:
            ps = sources.crossref_journal(j["name"], j["issn"], since, watch)
            log.info("%-45s %4d", j["name"], len(ps))
            add(ps)
        except Exception as e:  # noqa: BLE001
            log.warning("Crossref failed for %s: %s", j["name"], e)
            failed.append(j["name"])
        time.sleep(1)

    for a in cfg.get("authors", []):
        try:
            ps = sources.crossref_author(a["name"], since, watch)
            log.info("author %-38s %4d candidates", a["name"], len(ps))
            add(ps)
        except Exception as e:  # noqa: BLE001
            log.warning("Crossref author search failed for %s: %s", a["name"], e)
            failed.append(f"author {a['name']}")
        time.sleep(1)

    cats = cfg.get("arxiv", {}).get("categories", [])
    try:
        ps = sources.arxiv_categories(cats, since) if cats else []
        log.info("%-45s %4d", "arXiv " + ",".join(cats), len(ps))
        add(ps)
    except Exception as e:  # noqa: BLE001
        log.warning("arXiv listing failed: %s", e)
        failed.append("arXiv")
    try:
        ps = sources.arxiv_authors([a["name"] for a in cfg.get("authors", [])], since)
        log.info("%-45s %4d candidates", "arXiv authors", len(ps))
        for p in ps:
            p.in_watchlist = False        # off-category arXiv: require ORCID-safe matching rules
        add(ps)
    except Exception as e:  # noqa: BLE001
        log.warning("arXiv author search failed: %s", e)

    if failed:
        notes.append("Couldn’t reach: " + ", ".join(failed) + ". Those papers will be picked up tomorrow.")
    return list(found.values())


# ── main ─────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    cfg = tomllib.loads((ROOT / args.config).read_text())
    dcfg = cfg.get("digest", {})
    tz = ZoneInfo(dcfg.get("timezone", "America/New_York"))
    now = datetime.now(tz)
    today = now.date()
    state_path = ROOT / args.state
    st = load_state(state_path)
    first_run = not st["seen"]

    if not args.force and not args.dry_run:
        if st.get("last_sent") == today.isoformat():
            log.info("Digest for %s already sent; nothing to do.", today)
            return 0
        hh, mm = map(int, dcfg.get("send_after", "08:45").split(":"))
        if (now.hour, now.minute) < (hh, mm):
            log.info("It's %s in %s, before send_after=%s; skipping this run.", now.strftime("%H:%M"), tz, dcfg.get("send_after"))
            return 0

    lookback = int(dcfg.get("lookback_days", 7))
    since = today - timedelta(days=lookback)
    themes = load_themes(cfg.get("themes", []))
    followed = [FollowedAuthor.from_cfg(a) for a in cfg.get("authors", [])]
    notes: list[str] = []

    papers = collect(cfg, since, followed, notes)
    seen = st["seen"]
    # skip anything already emailed (also via the journal DOI of an arXiv preprint)
    papers = [p for p in papers if p.key not in seen and not (p.doi and f"doi:{p.doi}" in seen)]
    log.info("%d unseen candidates", len(papers))

    for p in papers:
        tag_followed(p, followed)
    # author-search results that aren't by a followed author are namesakes: drop them
    papers = [p for p in papers if p.in_watchlist or p.followed or p.followed_tentative]
    missing = [p for p in papers if not p.abstract_html]
    if missing:
        n1 = sources.fill_abstracts_semantic_scholar(missing)
        n2 = sources.fill_abstracts_openalex(missing)
        log.info("abstract back-fill: %d/%d (Semantic Scholar %d, OpenAlex %d)", n1 + n2, len(missing), n1, n2)

    wait = int(dcfg.get("abstract_wait_days", 2))
    first_cutoff = today - timedelta(days=2)
    chosen: list[Paper] = []
    today_s = today.isoformat()
    min_hits = int(dcfg.get("min_abstract_hits", 2))
    for p in papers:
        if first_run and p.created < first_cutoff:
            seen[p.key] = today_s          # first run: don't send a whole week of backlog
            continue
        assign_themes(p, themes, min_abstract_hits=min_hits)
        if p.followed_tentative and p.theme:
            p.followed += p.followed_tentative     # name match + on-topic: trust it
        if not p.in_watchlist and not p.followed:
            seen[p.key] = today_s                  # namesake, off-topic
            continue
        if p.followed or p.theme:
            chosen.append(p)
            continue
        if not p.abstract_html:
            first = st["pending"].setdefault(p.key, today_s)
            if (today - date.fromisoformat(first)).days < wait:
                continue                   # give the abstract time to show up
        seen[p.key] = today_s              # judged; not relevant

    # Group into sections
    def sort_key(p: Paper):
        return (p.is_preprint, -(p.published or p.created).toordinal(), p.venue, p.title_text)

    sections = []
    fol = sorted([p for p in chosen if p.followed], key=sort_key)
    sections.append((FOLLOWED, FOLLOWED_COLOR, fol, True))
    for t in sorted(themes, key=lambda t: t.order):
        ps = sorted([p for p in chosen if not p.followed and p.theme == t.name], key=sort_key)
        sections.append((t.name, t.color, ps, False))

    journals = len(cfg["journals"])
    srcs = f"Sources: {journals} journals via Crossref · arXiv ({', '.join(cfg.get('arxiv', {}).get('categories', []))})"
    subject, html_body, text_body = render(
        sections, today=today, title=dcfg.get("title", "Daily Paper Digest"), themes=themes,
        followed=followed, max_authors=int(dcfg.get("max_authors_shown", 8)), notes=notes, sources_line=srcs)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_body)
    out.with_suffix(".txt").write_text(text_body)
    log.info("wrote %s (%.0f KB) with %d papers", out, len(html_body.encode()) / 1024, len(chosen))

    if args.dry_run:
        log.info("dry run: not sending email, not updating state")
        return 0
    if not chosen and not dcfg.get("send_when_empty", True):
        log.info("nothing to send")
    else:
        send_email(subject, html_body, text_body)
    for p in chosen:
        seen[p.key] = today_s
        st["pending"].pop(p.key, None)
    for k in list(st["pending"]):
        if k in seen:
            st["pending"].pop(k)
    st["last_sent"] = today_s
    save_state(state_path, st, today)
    return 0


def send_email(subject: str, html_body: str, text_body: str) -> None:
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    to = os.environ.get("DIGEST_RECIPIENT") or sender
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Paper Digest", sender))
    msg["To"] = to
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=60) as s:
        s.login(sender, password)
        s.send_message(msg)
    log.info("email sent")


def main() -> int:
    ap = argparse.ArgumentParser(description="Send the daily paper digest.")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--state", default="state/state.json")
    ap.add_argument("--out", default="out/digest.html")
    ap.add_argument("--force", action="store_true", help="ignore send time and 'already sent today'")
    ap.add_argument("--dry-run", action="store_true", help="build the email but don't send or save state")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
