"""Structure-preserving page extraction: readable text, HTML tables and tables embedded as JSON.

Why: trafilatura's article text drops tables, and many sports/finance pages render their data from
JSON embedded in the page (Next.js `__NEXT_DATA__` and similar). Flattening a standings table into
text loses which number belongs to which team; here every table keeps its rows, columns and a
label (caption or nearest heading, e.g. "Trendyol Süper Lig" vs "Şampiyonlar Ligi").
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("lazos.extract")

MAX_TABLES = 12
MAX_ROWS = 60
_WS = re.compile(r"\s+")
_BOILERPLATE = re.compile(
    r"çerez|cookie|kişisel veri|kvkk|gizlilik politika|aydınlatma metni|tümünü kabul|tümünü reddet|abone ol|bildirim(ler)?i aç"
    r"|reklam|üye girişi|giriş yap|şifremi unuttum|uygulamayı indir|app store|google play|tüm hakları saklıdır",
    re.IGNORECASE,
)


@dataclass
class Table:
    label: str
    headers: list[str]
    rows: list[list[str]]
    origin: str = "html"  # "html" | "json"
    path: str = ""  # JSON path, for diagnostics

    def to_markdown(self, rows: list[list[str]] | None = None) -> str:
        body = self.rows if rows is None else rows
        lines = [f"Tablo: {self.label}"] if self.label else []
        lines.append("| " + " | ".join(self.headers) + " |")
        lines.append("|" + "---|" * len(self.headers))
        lines += ["| " + " | ".join(r) + " |" for r in body]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "headers": self.headers, "rows": self.rows[:MAX_ROWS], "origin": self.origin}


@dataclass
class Page:
    url: str
    title: str = ""
    text: str = ""
    published: str | None = None
    tables: list[Table] = field(default_factory=list)


def clean_cell(text: str) -> str:
    """Collapse whitespace and undo responsive duplicates ("Galatasaray Galatasaray", "1 1"), not "77"."""
    t = _WS.sub(" ", text).strip()
    half = len(t) // 2
    if len(t) % 2 == 1 and t[half] == " " and t[:half] == t[half + 1 :]:
        t = t[:half]
    return t.replace("−", "-")


def _label_for(node: Any) -> str:
    """Caption, or the nearest short text before the table (walking up a few ancestors)."""
    cap = node.find("caption")
    if cap is not None and cap.text_content().strip():
        return clean_cell(cap.text_content())[:120]
    cur = node
    for _ in range(6):
        prev = cur.getprevious()
        while prev is not None:
            if isinstance(prev.tag, str) and prev.tag not in ("script", "style", "noscript"):  # skip comments
                txt = clean_cell(prev.text_content())
                if 2 <= len(txt) <= 120:
                    return txt
                if len(txt) > 120:
                    break  # a paragraph, not a heading
            prev = prev.getprevious()
        cur = cur.getparent()
        if cur is None:
            break
    return ""


def html_tables(doc: Any) -> list[Table]:
    out: list[Table] = []
    for node in doc.iter("table"):
        if node.xpath(".//table"):
            continue  # layout table wrapping other tables (the data lives in the inner one, e.g. tff.org)
        rows = []
        for tr in node.xpath(".//tr"):
            cells = [clean_cell(c.text_content()) for c in tr.xpath("./th|./td")]
            if any(cells):
                rows.append(cells)
        if len(rows) < 3 or max(len(r) for r in rows) < 2:
            continue
        has_th = bool(node.xpath(".//tr[1]/th")) or bool(node.xpath(".//thead"))
        headers = rows[0] if has_th or not any(c.isdigit() for c in rows[0]) else [f"s{i + 1}" for i in range(len(rows[0]))]
        body = rows[1:] if headers is rows[0] else rows
        out.append(Table(_label_for(node), headers, body[:MAX_ROWS]))
        if len(out) >= MAX_TABLES:
            break
    return out


# ── tables embedded as JSON ──────────────────────────────────────────────
_RANK_KEYS = ("position", "rank", "pos", "standing", "sira", "order")
_POINT_KEYS = ("point", "points", "pts", "puan")
_COLUMNS = (  # (header, candidate keys)
    ("Sıra", _RANK_KEYS),
    ("O", ("played", "matches", "matchesPlayed", "gamesPlayed", "playedGames", "games", "mp")),
    ("G", ("won", "win", "wins")),
    ("B", ("draw", "draws", "drawn")),
    ("M", ("lost", "loss", "losses")),
    ("Av", ("goalDifference", "goal_difference", "goals_average", "goalDiff", "gd", "averaj")),
    ("P", _POINT_KEYS),
)
_SPLIT_KEYS = re.compile(r"(^|[/_.])(home|away|ev|deplasman|form|last\w*)$", re.IGNORECASE)
_OVERALL_KEYS = re.compile(r"(^|[/_.])(general|overall|total|all|standings?|table)$", re.IGNORECASE)


def _name(item: dict[str, Any]) -> str | None:
    for key in ("team", "club", "participant", "competitor"):
        v = item.get(key)
        if isinstance(v, dict):
            for k in ("name", "shortName", "displayName", "title"):
                if isinstance(v.get(k), str):
                    return v[k]
        if isinstance(v, str):
            return v
    for k in ("teamName", "team_name", "clubName", "name", "title"):
        if isinstance(item.get(k), str):
            return item[k]
    return None


def _first(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in item and isinstance(item[k], (int, float, str)) and str(item[k]).lstrip("+-").isdigit():
            return item[k]
    return None


def _json_label(stack: list[dict[str, Any]]) -> str:
    for obj in reversed(stack):
        for k in ("competitionName", "leagueName", "tournamentName", "seasonName", "title", "name"):
            if isinstance(obj.get(k), str) and len(obj[k]) <= 80:
                return obj[k]
    return ""


def json_tables(doc: Any) -> list[Table]:
    """Find lists of standing-like records (rank + points + a team name) inside page JSON."""
    found: list[Table] = []
    scripts = doc.xpath("//script[@type='application/json' or @id='__NEXT_DATA__' or @type='application/ld+json']")

    def walk(obj: Any, path: str, stack: list[dict[str, Any]], depth: int) -> None:
        if depth > 14 or len(found) >= MAX_TABLES:
            return
        if isinstance(obj, list):
            dicts = [x for x in obj if isinstance(x, dict)]
            if len(dicts) >= 4 and len(dicts) == len(obj):
                sample = dicts[0]
                if _first(sample, _RANK_KEYS) is not None and _first(sample, _POINT_KEYS) is not None and _name(sample):
                    if not _SPLIT_KEYS.search(path):
                        cols = [(h, keys) for h, keys in _COLUMNS if any(k in sample for k in keys)]
                        headers = [cols[0][0], "Takım", *[h for h, _ in cols[1:]]]
                        rows = [
                            [str(_first(d, cols[0][1]) or ""), _name(d) or "", *[str(_first(d, keys) or "0") for _, keys in cols[1:]]]
                            for d in dicts[:MAX_ROWS]
                        ]
                        found.append(Table(_json_label(stack), headers, rows, origin="json", path=path))
                    return
            for i, v in enumerate(obj[:200]):
                walk(v, f"{path}[{i}]", stack, depth + 1)
        elif isinstance(obj, dict):
            stack.append(obj)
            for k, v in obj.items():
                walk(v, f"{path}/{k}", stack, depth + 1)
            stack.pop()

    for script in scripts:
        raw = (script.text or "").strip()
        if not raw or len(raw) > 3_000_000:
            continue
        try:
            walk(json.loads(raw), "", [], 0)
        except (ValueError, RecursionError):
            continue
    # An overall table ("general") beats unlabeled ones; home/away splits were already skipped.
    found.sort(key=lambda t: 0 if _OVERALL_KEYS.search(t.path) else 1)
    return found


def _published(html: str, doc: Any) -> str | None:
    for xp in (
        "//meta[@property='article:modified_time']/@content",
        "//meta[@property='article:published_time']/@content",
        "//meta[@name='date']/@content",
        "//time/@datetime",
    ):
        v = doc.xpath(xp)
        if v:
            return str(v[0])[:25]
    try:
        from htmldate import find_date

        return find_date(html, extensive_search=False, original_date=False)
    except Exception:  # htmldate is best-effort
        return None


def parse_page(html: str, url: str) -> Page:
    """Blocking (lxml/trafilatura); call via asyncio.to_thread."""
    import lxml.html
    import trafilatura

    page = Page(url=url)
    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return page
    title = doc.findtext(".//title") or ""
    page.title = clean_cell(title)[:200]
    page.published = _published(html, doc)
    embedded = json_tables(doc)  # before scripts are dropped
    for el in doc.xpath("//style|//script|//noscript"):
        el.drop_tree()
    page.tables = embedded + html_tables(doc)
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True, deduplicate=True)
    page.text = "\n".join(p for p in (text or "").splitlines() if not _BOILERPLATE.search(p))
    return page


_WORD = re.compile(r"\w{3,}", re.UNICODE)


def _terms(text: str) -> set[str]:
    return {w.casefold()[:6] for w in _WORD.findall(text)}  # crude stemming helps Turkish suffixes


def select_passages(text: str, query: str, max_chars: int) -> str:
    """Pick the paragraphs most related to the query (boilerplate removed), keeping their original order."""
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if len(p.strip()) > 40 and not _BOILERPLATE.search(p)]
    if not paragraphs:
        return ""
    q = _terms(query)
    scored = [(len(q & _terms(p)) + (0.5 if i < 3 else 0), i, p) for i, p in enumerate(paragraphs)]
    scored.sort(key=lambda x: (-x[0], x[1]))
    chosen: list[tuple[int, str]] = []
    total = 0
    for _, i, p in scored:
        if total + len(p) > max_chars and chosen:
            continue
        chosen.append((i, p[: max_chars - total]))
        total += min(len(p), max_chars - total)
        if total >= max_chars:
            break
    chosen.sort()
    return "\n".join(p for _, p in chosen)
