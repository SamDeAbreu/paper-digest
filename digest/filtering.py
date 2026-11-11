"""Keyword themes and followed-author matching."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sources import Author, Paper
from .text import normalize


# ── keywords ─────────────────────────────────────────────────────────────────

def keyword_to_regex(kw: str) -> str:
    """Turn a friendly keyword ("sea ice", "turbulen*", "re:...") into a regex
    that runs on normalize()d text."""
    if kw.startswith("re:"):
        return kw[3:]
    kw = normalize(kw.strip())
    star = kw.endswith("*")
    kw = kw.rstrip("*")
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", kw) if p]
    body = r"[\s\-]*".join(parts) if len(parts) > 1 else parts[0]
    return rf"\b{body}" + (r"\w*" if star else r"\b")


@dataclass
class Theme:
    name: str
    color: str
    priority: int
    order: int
    patterns: list[re.Pattern] = field(default_factory=list)

    def matches(self, title: str, abstract: str, min_abstract_hits: int) -> bool:
        """In the title once, or in the abstract at least `min_abstract_hits` times
        (a single passing mention in an abstract usually isn't what the paper is about)."""
        if any(p.search(title) for p in self.patterns):
            return True
        hits = sum(len(p.findall(abstract)) for p in self.patterns)
        return hits >= min_abstract_hits


def load_themes(cfg: list[dict]) -> list[Theme]:
    themes = []
    for i, t in enumerate(cfg):
        pats = [re.compile(keyword_to_regex(k), re.I) for k in t.get("keywords", [])]
        themes.append(Theme(t["name"], t.get("color", "#2563eb"), t.get("priority", 100 + i), i, pats))
    return themes


def assign_themes(paper: Paper, themes: list[Theme], *, min_abstract_hits: int = 2) -> None:
    title, abstract = normalize(paper.title_text), normalize(paper.abstract_text)
    hits = sorted((t for t in themes if t.matches(title, abstract, min_abstract_hits)), key=lambda t: t.priority)
    paper.theme = hits[0].name if hits else None
    paper.other_themes = [t.name for t in sorted(hits[1:], key=lambda t: t.order)]


# ── authors ──────────────────────────────────────────────────────────────────

@dataclass
class FollowedAuthor:
    name: str
    family: str
    given: list[str]
    orcid: str | None
    allow_initials: bool
    exclude: list[str]

    @classmethod
    def from_cfg(cls, a: dict) -> "FollowedAuthor":
        name = a["name"]
        family = a.get("family") or name.split()[-1]
        given = a.get("given") or [name[: len(name) - len(family)].strip()]
        return cls(
            name=name,
            family=normalize(family),
            given=[normalize(g) for g in given if g],
            orcid=(a.get("orcid") or "").strip() or None,
            allow_initials=a.get("allow_initials", True),
            exclude=[normalize(x).replace("-", " ") for x in a.get("exclude", [])],
        )

    def name_matches(self, au: Author) -> bool:
        full = normalize(f"{au.given} {au.family}").replace("-", " ").replace(".", ". ")
        full = re.sub(r"\s+", " ", full).strip()
        if any(full == x for x in self.exclude):
            return False
        fam = self.family.replace("-", " ")
        if not (full.endswith(" " + fam) or full == fam):
            return False
        first_names = full[: -len(fam)].strip()
        if not first_names:
            return False
        first = first_names.split()[0].rstrip(".")
        for g in self.given:
            g_first = g.replace("-", " ").split()[0]
            if first == g_first:
                return True
            if self.allow_initials and len(first) == 1 and first == g_first[0]:
                return True
        return False

    def match_level(self, au: Author, paper: Paper) -> int:
        """2 = confirmed, 1 = plausible (only counts if the paper is on-topic), 0 = no."""
        if self.orcid and au.orcid:
            return 2 if au.orcid == self.orcid else 0
        if not self.name_matches(au):
            return 0
        if paper.in_watchlist:
            return 2
        # Outside the watch list with no ORCID on record a name alone may be a
        # namesake in another field: accept full first names only, and only if
        # the paper also matches one of the themes.
        first = normalize(au.given).replace(".", ". ").split()[:1]
        if not first or len(first[0].rstrip(".")) < 2:
            return 0
        return 1

    def matches(self, au: Author, paper: Paper) -> bool:
        return self.match_level(au, paper) == 2


def tag_followed(paper: Paper, authors: list[FollowedAuthor]) -> None:
    confirmed, tentative = [], []
    for fa in authors:
        lvl = max((fa.match_level(au, paper) for au in paper.authors), default=0)
        if lvl == 2:
            confirmed.append(fa.name)
        elif lvl == 1:
            tentative.append(fa.name)
    paper.followed = confirmed
    paper.followed_tentative = tentative


def followed_author_indices(paper: Paper, authors: list[FollowedAuthor]) -> set[int]:
    names = set(paper.followed)
    return {i for i, au in enumerate(paper.authors)
            if any(fa.name in names and fa.match_level(au, paper) > 0 for fa in authors)}
