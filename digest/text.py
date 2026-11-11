"""Cleaning of titles and abstracts.

Publishers deliver abstracts as JATS XML (Crossref), HTML fragments or TeX
(arXiv). Everything is reduced to a small, email-safe HTML subset
(<i>, <b>, <sub>, <sup>) split into paragraphs, plus a plain-text version
used for keyword matching and the text/plain email part.
"""

from __future__ import annotations

import html
import re
import unicodedata
from html.parser import HTMLParser

# Sections that aren't the scientific abstract itself.
_DROP_SECTIONS = re.compile(
    r"plain\s+language\s+summary|key\s+points|significance|graphical\s+abstract|highlights|teaser",
    re.I,
)

_INLINE = {
    "i": "i", "em": "i", "italic": "i",
    "b": "b", "strong": "b", "bold": "b",
    "sub": "sub", "sup": "sup",
}
_SKIP = {"math", "inline-graphic", "graphic", "alt-text", "object-id", "label", "xref", "fig", "table-wrap"}
_PARA = {"p", "list-item", "li", "br", "div"}


def _local(tag: str) -> str:
    return tag.split(":", 1)[-1].lower()


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paras: list[list[str]] = [[]]
        self.skip_depth = 0
        self.tex: list[str] | None = None
        self.title: list[str] | None = None
        self.sec_stack: list[bool] = []  # True = section is dropped
        self.open_inline: list[str] = []

    # helpers
    @property
    def dropping(self) -> bool:
        return self.skip_depth > 0 or any(self.sec_stack)

    def _emit(self, s: str) -> None:
        if not self.dropping:
            self.paras[-1].append(s)

    def _break(self) -> None:
        if self.paras[-1]:
            self.paras.append([])

    # parser callbacks
    def handle_starttag(self, tag, attrs):
        t = _local(tag)
        if t in _SKIP:
            self.skip_depth += 1
        elif t == "tex-math":
            self.tex = []
        elif t == "title":
            self.title = []
        elif t == "sec":
            self.sec_stack.append(False)
        elif t in _PARA:
            self._break()
            if t in ("list-item", "li") and not self.dropping:
                self.paras[-1].append("• ")
        elif t in _INLINE and self.skip_depth == 0:
            self._emit(f"<{_INLINE[t]}>")
            self.open_inline.append(_INLINE[t])

    def handle_startendtag(self, tag, attrs):
        if _local(tag) == "br":
            self._break()

    def handle_endtag(self, tag):
        t = _local(tag)
        if t in _SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif t == "tex-math":
            if self.tex is not None:
                self._emit(tex_to_html(html.escape("".join(self.tex), quote=False)))
            self.tex = None
        elif t == "title":
            text = "".join(self.title or []).strip()
            self.title = None
            if self.sec_stack and _DROP_SECTIONS.search(text):
                self.sec_stack[-1] = True
            # Titles are never shown: "Abstract", "Background", ... add nothing.
        elif t == "sec":
            if self.sec_stack:
                self.sec_stack.pop()
            self._break()
        elif t in _PARA:
            self._break()
        elif t in _INLINE and self.skip_depth == 0:
            want = _INLINE[t]
            if want in self.open_inline:
                self.open_inline.remove(want)
                self._emit(f"</{want}>")

    def handle_data(self, data):
        if self.tex is not None:
            self.tex.append(data)
        elif self.title is not None:
            self.title.append(data)
        else:
            self._emit(html.escape(data, quote=False))


def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+(<su[bp]>)", r"\1", s)               # "m s <sup>-1</sup>" -> "m s<sup>-1</sup>"
    s = re.sub(r"(<su[bp]>)\s+", r"\1", s)
    s = re.sub(r"\s+(</su[bp]>|</i>|</b>)", r"\1", s)
    s = re.sub(r"\s+([,.;:!?)\]])", r"\1", s)             # no space before punctuation
    s = re.sub(r"([(\[])\s+", r"\1", s)
    s = re.sub(r"<(i|b|sub|sup)>\s*</\1>", "", s)          # empty tags
    return s


def clean_markup(raw: str | None) -> list[str]:
    """JATS/HTML fragment -> list of safe-HTML paragraphs."""
    if not raw:
        return []
    raw = re.sub(r"\$([^$]{1,300})\$", lambda m: _TEX_MARK.format(html.escape(m.group(1))), raw)
    p = _Cleaner()
    p.feed(raw)
    p.close()
    paras = [_tidy("".join(x)) for x in p.paras]
    paras = [x for x in paras if x and not re.fullmatch(r"(?i)abstract[.:]?", strip_tags(x))]
    if paras:
        paras[0] = re.sub(r"(?i)^abstract[.:]?\s+", "", paras[0])
    return paras


_TEX_MARK = "<tex-math>{}</tex-math>"


def clean_tex_text(raw: str | None) -> list[str]:
    """arXiv-style plain text with inline $TeX$ -> safe-HTML paragraphs."""
    if not raw:
        return []
    out = []
    for para in re.split(r"\n\s*\n", raw.strip()):
        pieces = re.split(r"(\$\$?[^$]+\$\$?)", para)
        s = "".join(
            tex_to_html(html.escape(x.strip("$"), quote=False)) if x.startswith("$") else html.escape(x, quote=False)
            for x in pieces
        )
        s = _tidy(s)
        if s:
            out.append(s)
    return out


def clean_title(raw: str | None) -> str:
    if not raw:
        return "(untitled)"
    if "$" in raw and "<" not in raw:
        paras = clean_tex_text(raw)
    else:
        paras = clean_markup(raw)
    return " ".join(paras) or "(untitled)"


def strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))


# ── TeX → readable HTML ──────────────────────────────────────────────────────

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ",
    "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_SYMBOLS = {
    "sim": "∼", "approx": "≈", "simeq": "≃", "times": "×", "cdot": "·", "pm": "±", "mp": "∓",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "lesssim": "≲", "gtrsim": "≳", "ll": "≪", "gg": "≫",
    "neq": "≠", "ne": "≠", "propto": "∝", "infty": "∞", "partial": "∂", "nabla": "∇",
    "rightarrow": "→", "to": "→", "leftarrow": "←", "Rightarrow": "⇒", "circ": "°", "degree": "°",
    "ell": "ℓ", "hbar": "ħ", "langle": "⟨", "rangle": "⟩", "int": "∫", "sum": "∑", "prod": "∏",
    "equiv": "≡", "perp": "⊥", "parallel": "∥", "in": "∈", "prime": "′", "%": "%",
    "odot": "⊙", "star": "⋆", "dots": "…", "ldots": "…", "cdots": "⋯", "vert": "|", "|": "‖",
}
_STRIP_CMDS = r"mathrm|mathit|mathbf|mathsf|mathcal|mathbb|text|textrm|textit|textbf|boldsymbol|bm|rm|it|bf|operatorname|textsf|emph|mbox|hat|bar|tilde|vec|dot|ddot|overline|widehat|widetilde|left|right|big|Big|bigg|Bigg"


def tex_to_html(tex: str) -> str:
    """Best-effort rendering of short inline TeX. Input must already be HTML-escaped."""
    s = tex.strip()
    s = re.sub(r"\\[,;:! ]|\\quad|\\qquad|~", " ", s)
    s = re.sub(r"\\(?:textit|mathit|emph)\{([^{}]*)\}", r"<i>\1</i>", s)
    for _ in range(3):
        s = re.sub(r"\\(?:d|t)?frac\{([^{}]*)\}\{([^{}]*)\}", r"\1/\2", s)
        s = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", s)
        s = re.sub(rf"\\(?:{_STRIP_CMDS})\b\s*", "", s)
    s = re.sub(r"\\([A-Za-z]+)", lambda m: _GREEK.get(m.group(1), _SYMBOLS.get(m.group(1), m.group(1))), s)
    s = re.sub(r"\\([%|])", lambda m: _SYMBOLS.get(m.group(1), m.group(1)), s)
    s = s.replace("^°", "°").replace("^{°}", "°")
    for _ in range(2):
        s = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", s)
        s = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", s)
    s = re.sub(r"\^([\w−\-+′])", r"<sup>\1</sup>", s)
    s = re.sub(r"_(\w)", r"<sub>\1</sub>", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"(?<=\w)-(?=\d)", "−", s)
    return re.sub(r"\s+", " ", s).strip()


# ── normalization for matching ───────────────────────────────────────────────

_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u00ad"


def normalize(s: str) -> str:
    """Lowercase ASCII with unified dashes, used for keyword and name matching."""
    s = s.translate({ord(c): "-" for c in _DASHES})
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).lower()
