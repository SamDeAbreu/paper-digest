"""HTML + plain-text email rendering.

Table layout with inline styles so it renders the same in Outlook (desktop &
web), Gmail and Apple Mail. Kept deliberately light to stay well under
Gmail's ~100 KB clipping limit.
"""

from __future__ import annotations

import html
from datetime import date

from .filtering import FollowedAuthor, Theme, followed_author_indices
from .sources import Paper
from .text import strip_tags

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK, BODY, MUTED, LINE, BG, CARD = "#0f172a", "#334155", "#64748b", "#e2e8f0", "#f1f5f9", "#ffffff"
ACCENT = "#0f766e"
AUTHOR_COLOR = "#b45309"


def _e(s: str) -> str:
    return html.escape(s, quote=True)


def _fmt_date(d: date | None) -> str:
    return f"{d:%b} {d.day}, {d.year}" if d else ""


def _tint(hex_color: str, alpha: float) -> str:
    """Solid colour = hex blended onto white (Outlook has no rgba)."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: round(255 - (255 - c) * alpha)  # noqa: E731
    return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"


def _pill(text: str, color: str, *, filled: bool = False) -> str:
    bg = color if filled else _tint(color, 0.12)
    fg = "#ffffff" if filled else color
    return (f'<span style="display:inline-block;padding:2px 9px;margin:0 4px 4px 0;border-radius:999px;'
            f'background:{bg};color:{fg};font-size:11px;font-weight:600;line-height:18px;'
            f'white-space:nowrap">{_e(text)}</span>')


def _authors_html(p: Paper, followed: list[FollowedAuthor], max_shown: int) -> str:
    hl = followed_author_indices(p, followed)
    n = len(p.authors)
    show = list(range(min(n, max_shown)))
    extra = sorted(i for i in hl if i >= max_shown)
    if n > max_shown and (n - 1) not in show and (n - 1) not in extra:
        extra.append(n - 1)            # last author is often the PI
    parts, prev = [], -1
    for i in show + extra:
        if i != prev + 1 and prev >= 0:
            parts.append("…")
        nm = _e(p.authors[i].display or "—")
        if i in hl:
            nm = f'<b style="color:{AUTHOR_COLOR};font-weight:700">{nm}</b>'
        parts.append(nm)
        prev = i
    s = ", ".join(parts).replace(", …,", " …")
    if n > len(show) + len(extra):
        s += f' <span style="color:{MUTED}">(+{n - len(show) - len(extra)} more)</span>'
    return s


def _card(p: Paper, color: str, themes_by_name: dict[str, Theme], followed: list[FollowedAuthor],
          max_authors: int, show_theme: bool) -> str:
    meta = [f'<span style="color:{color};font-weight:700">{_e(p.venue)}</span>']
    if p.published:
        meta.append(_e(_fmt_date(p.published)))
    meta_html = ' <span style="color:#cbd5e1">&nbsp;•&nbsp;</span> '.join(meta)
    badges = ""
    if p.is_preprint:
        badges += _pill("Preprint", "#64748b")
    for nm in p.followed:
        badges += _pill(f"★ {nm}", AUTHOR_COLOR)
    tags = [p.theme] if (show_theme and p.theme) else []
    tags += p.other_themes
    for t in tags:
        badges += _pill(t, themes_by_name[t].color if t in themes_by_name else MUTED)

    if p.abstract_html:
        abstract = "".join(
            f'<p style="margin:0 0 10px 0;font-size:14px;line-height:1.6;color:{BODY}">{para}</p>'
            for para in p.abstract_html)
    else:
        abstract = (f'<p style="margin:0 0 10px 0;font-size:13px;line-height:1.6;color:{MUTED};font-style:italic">'
                    f'The publisher didn’t provide an abstract. Open the paper to read it.</p>')

    links = [f'<a href="{_e(p.url)}" style="color:{ACCENT};text-decoration:none;font-weight:600">'
             f'{"View on arXiv" if p.is_preprint else "Read paper"}&nbsp;→</a>']
    if p.pdf_url:
        links.append(f'<a href="{_e(p.pdf_url)}" style="color:{ACCENT};text-decoration:none;font-weight:600">PDF</a>')
    if p.doi and not p.is_preprint:
        links.append(f'<span style="color:{MUTED}">doi:{_e(p.doi)}</span>')
    links_html = ' <span style="color:#cbd5e1">&nbsp;|&nbsp;</span> '.join(links)

    return f"""
<tr><td style="padding:0 0 14px 0">
 <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{CARD};border:1px solid {LINE};border-radius:12px;border-collapse:separate">
  <tr>
   <td width="4" style="width:4px;background:{color};border-radius:12px 0 0 12px;font-size:0;line-height:0">&nbsp;</td>
   <td style="padding:18px 22px 14px 18px;font-family:{FONT}">
    <div style="font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:{MUTED};margin:0 0 6px 0">{meta_html}</div>
    <div style="font-size:17px;line-height:1.35;font-weight:700;margin:0 0 6px 0"><a href="{_e(p.url)}" style="color:{INK};text-decoration:none">{p.title_html}</a></div>
    <div style="font-size:13px;line-height:1.5;color:{MUTED};margin:0 0 10px 0">{_authors_html(p, followed, max_authors)}</div>
    {f'<div style="margin:0 0 8px 0">{badges}</div>' if badges else ''}
    {abstract}
    <div style="font-size:13px;margin:4px 0 0 0">{links_html}</div>
   </td>
  </tr>
 </table>
</td></tr>"""


def _section_header(name: str, color: str, count: int, anchor: str) -> str:
    return f"""
<tr><td style="padding:22px 0 10px 2px;font-family:{FONT}">
 <a name="{anchor}" id="{anchor}"></a>
 <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
  <td width="10" style="width:10px"><div style="width:10px;height:10px;border-radius:5px;background:{color};font-size:0;line-height:0">&nbsp;</div></td>
  <td style="padding-left:10px;font-size:15px;font-weight:800;color:{INK};letter-spacing:-.01em">{_e(name)}</td>
  <td style="padding-left:8px;font-size:13px;color:{MUTED}">{count}</td>
 </tr></table>
</td></tr>"""


def render(sections: list[tuple[str, str, list[Paper], bool]], *, today: date, title: str,
           themes: list[Theme], followed: list[FollowedAuthor], max_authors: int,
           notes: list[str], sources_line: str) -> tuple[str, str, str]:
    """sections: (name, color, papers, show_theme_tag). Returns (subject, html, text)."""
    themes_by_name = {t.name: t for t in themes}
    total = sum(len(s[2]) for s in sections)
    n_auth = next((len(s[2]) for s in sections if s[0] == "From authors you follow"), 0)
    day = f"{today:%A}, {today:%B} {today.day}"

    subject = f"{title} · {today:%a %b} {today.day} · " + (
        f"{total} new paper{'s' if total != 1 else ''}" if total else "no new papers")
    if n_auth:
        subject += f" · ★ {n_auth} from followed authors"

    summary_bits = [f"<b style='color:{INK}'>{total}</b> new paper{'s' if total != 1 else ''}"]
    if n_auth:
        summary_bits.append(f"<b style='color:{AUTHOR_COLOR}'>{n_auth}</b> from authors you follow")
    preheader = " · ".join(f"{s[0]} {len(s[2])}" for s in sections if s[2]) or "Nothing new today"

    toc = "".join(
        f'<a href="#s{i}" style="text-decoration:none">{_pill(f"{name}  {len(ps)}", color)}</a>'
        for i, (name, color, ps, _) in enumerate(sections) if ps)

    rows = []
    for i, (name, color, ps, show_theme) in enumerate(sections):
        if not ps:
            continue
        rows.append(_section_header(name, color, len(ps), f"s{i}"))
        rows.extend(_card(p, color, themes_by_name, followed, max_authors, show_theme) for p in ps)
    if not total:
        rows.append(f'<tr><td style="padding:28px 24px;font-family:{FONT};font-size:15px;color:{BODY};'
                    f'background:{CARD};border:1px solid {LINE};border-radius:12px;text-align:center">'
                    f'No new papers matched your topics or authors today.</td></tr>')

    notes_html = "".join(f"<div style='margin-top:6px;color:#b45309'>⚠ {_e(n)}</div>" for n in notes)

    body = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>{_e(subject)}</title></head>
<body style="margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">{_e(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{BG}">
<tr><td align="center" style="padding:28px 12px 40px 12px">
<!--[if mso]><table role="presentation" width="680" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:680px">
<tr><td style="padding:0 2px 18px 2px;font-family:{FONT}">
  <div style="font-size:12px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:{ACCENT}">{_e(title)}</div>
  <div style="font-size:26px;line-height:1.25;font-weight:800;color:{INK};letter-spacing:-.02em;margin:4px 0 6px 0">{_e(day)}</div>
  <div style="font-size:14px;color:{MUTED};margin:0 0 14px 0">{' &nbsp;·&nbsp; '.join(summary_bits)}</div>
  <div>{toc}</div>
</td></tr>
{''.join(rows)}
<tr><td style="padding:22px 4px 0 4px;font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED};text-align:center">
  {_e(sources_line)}<br>Change topics, authors and journals in <code>config.toml</code>.
  {notes_html}
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
</body></html>"""

    text = [subject, "=" * len(subject), ""]
    for name, _c, ps, _s in sections:
        if not ps:
            continue
        text += [f"## {name} ({len(ps)})", ""]
        for p in ps:
            text.append(p.title_text)
            text.append(", ".join(a.display for a in p.authors[:max_authors]) + (" et al." if len(p.authors) > max_authors else ""))
            text.append(f"{p.venue} · {_fmt_date(p.published)} · {p.url}")
            text.append("")
            text.append("\n\n".join(strip_tags(x) for x in p.abstract_html) or "(no abstract available)")
            text += ["", "-" * 40, ""]
    if not total:
        text.append("No new papers matched your topics or authors today.")
    text += ["", *(f"! {n}" for n in notes)]
    return subject, body, "\n".join(text)
