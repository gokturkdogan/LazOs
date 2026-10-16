"""Deterministic evidence extraction and verification for structured questions (league standings).

The model is good at phrasing an answer and bad at reconstructing which number belongs to which
team in a flattened table, so standings are parsed here, validated (right competition, right
season, values present on the page) and cross-checked between independent sources before the
model sees a single, explicit fact. Nothing is invented: a value that isn't in a source is absent.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.research.extract import Table
from app.research.intent import ResearchIntent, competition_in, fold, season_tokens, seasons_in


@dataclass
class StandingRow:
    rank: int
    team: str
    played: int | None = None
    won: int | None = None
    drawn: int | None = None
    lost: int | None = None
    goal_diff: int | None = None
    points: int | None = None

    def describe(self) -> str:
        bits = [f"{self.rank}. sıra"]
        if self.points is not None:
            bits.append(f"{self.points} puan")
        if self.played is not None:
            wdl = f" ({self.won}G {self.drawn}B {self.lost}M)" if None not in (self.won, self.drawn, self.lost) else ""
            bits.append(f"{self.played} maç{wdl}")
        if self.goal_diff is not None:
            bits.append(f"averaj {self.goal_diff:+d}")
        return ", ".join(bits)


@dataclass
class Observation:
    """One source's standings table (or snippet row) that passed validation."""

    url: str
    domain: str
    origin: str  # "table" | "json" | "snippet"
    label: str
    rows: list[StandingRow]
    authority: int = 0

    @property
    def max_played(self) -> int:
        return max((r.played or 0) for r in self.rows) if self.rows else 0


@dataclass
class Evidence:
    question_type: str
    competition: str
    season: str
    want: str
    entity: str | None
    status: str  # verified | single_source | conflict | insufficient
    row: StandingRow | None = None
    table: list[StandingRow] = field(default_factory=list)
    source_url: str | None = None
    agreeing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    current_season: str = ""
    retrieved_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))

    @property
    def found(self) -> bool:
        return self.row is not None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["table"] = d["table"][:20]
        return d


# ── table parsing ─────────────────────────────────────────────────────────
_INT = re.compile(r"[+-]?\d+")


def _int(cell: str) -> int | None:
    m = _INT.search(cell.replace("−", "-").replace("–", "-"))
    return int(m.group(0)) if m else None


_RANK_PREFIX = re.compile(r"^\s*(\d{1,2})\s*[.)-]\s*(?=\D)")


def _text_column(rows: list[list[str]]) -> int | None:
    """An unnamed team column: the first column whose cells are mostly words, not numbers."""
    if not rows:
        return None
    width = min(len(r) for r in rows[:10])
    for i in range(width):
        cells = [r[i] for r in rows[:10]]
        if sum(bool(re.search(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3,}", c)) for c in cells) >= max(3, len(cells) * 0.7):
            return i
    return None


def _columns(headers: list[str], rows: list[list[str]] | None = None) -> dict[str, int] | None:
    h = [fold(x).strip(" .:") for x in headers]
    turkish = any(x.startswith("takim") or x in ("o", "g", "b", "m", "puan", "av") for x in h)
    col: dict[str, int] = {}

    def first(names: tuple[str, ...], *, last: bool = False, prefix: bool = False) -> int | None:
        idx = [i for i, x in enumerate(h) if (x.startswith(names) if prefix else x in names)]
        return (idx[-1] if last else idx[0]) if idx else None

    team = first(("takim", "team", "kulup", "club", "takimlar"), prefix=True)
    if team is None and turkish:
        team = _text_column(rows or [])  # e.g. tff.org: unnamed column holding "1.GALATASARAY A.Ş."
    if team is None:
        return None
    col["team"] = team
    rank = first(("#", "sira", "s", "no", "pos", "position", "rk", "sira no"))
    if rank is not None:
        col["rank"] = rank
    points = first(("p", "puan", "pts", "points", "puanlar"), last=True) if turkish else first(("pts", "points", "pt"), last=True)
    if points is None:
        return None
    col["points"] = points
    for key, names in (
        ("played", ("o", "om", "oyn", "oynanan", "mac") if turkish else ("p", "pld", "mp", "gp", "played", "m")),
        ("won", ("g", "gal") if turkish else ("w", "won")),
        ("drawn", ("b", "ber") if turkish else ("d", "drawn")),
        ("lost", ("m", "mag") if turkish else ("l", "lost")),
        ("goal_diff", ("av", "averaj", "gf", "+/-", "a.") if turkish else ("gd", "+/-", "diff")),
    ):
        i = first(names)
        if i is not None and i not in col.values():
            col[key] = i
    return col


def parse_standings(table: Table) -> list[StandingRow]:
    col = _columns(table.headers, table.rows)
    if not col:
        return []
    out: list[StandingRow] = []
    for pos, row in enumerate(table.rows, start=1):
        if len(row) <= max(col.values()):
            continue
        team = re.sub(r"\s*\((Ş|K|S|C|E|Q|R)\)$|\s*\[.*?\]$", "", row[col["team"]]).strip()
        prefix = _RANK_PREFIX.match(team)
        if prefix:
            team = team[prefix.end() :].strip()
        points = _int(row[col["points"]])
        if not team or points is None:
            continue
        rank = _int(row[col["rank"]]) if "rank" in col else (int(prefix.group(1)) if prefix else None)
        out.append(
            StandingRow(
                rank=rank if rank is not None and 0 < rank <= 60 else pos,
                team=team,
                played=_int(row[col["played"]]) if "played" in col else None,
                won=_int(row[col["won"]]) if "won" in col else None,
                drawn=_int(row[col["drawn"]]) if "drawn" in col else None,
                lost=_int(row[col["lost"]]) if "lost" in col else None,
                goal_diff=_int(row[col["goal_diff"]]) if "goal_diff" in col else None,
                points=points,
            )
        )
    # A league table: enough rows, ranks increasing, points never increasing down the table.
    if len(out) < 4 or any(b.points > a.points for a, b in itertools.pairwise(out) if a.points is not None and b.points is not None):
        return []
    return out


def team_matches(entity: str, team: str) -> bool:
    e, t = fold(entity), fold(team)
    return t == e or t.startswith(e) or e in t.split() or (len(e) >= 5 and e in t)


def check_context(intent: ResearchIntent, label: str, page_title: str) -> str | None:
    """Why a table can't be about the requested competition/season (None when it fits)."""
    target = intent.competition
    for text in (label, page_title):
        comp = competition_in(text)
        if comp and target and comp.key != target.key:
            return f"farklı turnuva ({comp.name})"
        if comp:
            break
    seasons = seasons_in(f"{label} {page_title}")
    if intent.season and seasons and intent.season not in seasons:
        return f"farklı sezon ({', '.join(sorted(seasons))})"
    return None


# ── snippets ─────────────────────────────────────────────────────────────
_ROW_SNIPPET = re.compile(
    r"(?:^|[.,;] )(\d{1,2}), ([^\d,]{3,40}?), (\d{1,2}), (\d{1,2}), (\d{1,2}), (\d{1,2}), (\d{1,3}), (\d{1,3}), ([+\-−]?\d{1,3}), (\d{1,3})\b"
)


def snippet_rows(snippet: str, entity: str | None) -> list[StandingRow]:
    """Rows from search snippets: Wikipedia-style "1, Galatasaray, 7, 5, 1, 1, 16, 11, +5, 16" and
    sentences like "Galatasaray … puan durumunda 1. sırada ve 16 puanı var (7 maç)"."""
    rows = [
        StandingRow(int(m[1]), m[2].strip(), int(m[3]), int(m[4]), int(m[5]), int(m[6]), _int(m[9]), int(m[10]))
        for m in _ROW_SNIPPET.finditer(snippet)
    ]
    if entity:
        for sentence in re.split(r"(?<=[!?])\s+|(?<=[^\d\s][.])\s+(?=[A-ZÇĞİÖŞÜ])", snippet):  # "1. sırada" is not a sentence end
            if not team_matches(entity, sentence.split(",")[0]) and fold(entity) not in fold(sentence):
                continue
            rank = re.search(r"(\d{1,2})\.\s*sırada", sentence)
            points = re.search(r"(\d{1,3})\s*puan", sentence)
            played = re.search(r"(\d{1,2})\s*maç", sentence)
            if rank and points:
                rows.append(StandingRow(int(rank[1]), entity, _int(played[1]) if played else None, points=int(points[1])))
    return rows


# ── verification ─────────────────────────────────────────────────────────
def _pick(obs: Observation, intent: ResearchIntent) -> StandingRow | None:
    if intent.want == "leader" or not intent.entity:
        return next((r for r in obs.rows if r.rank == 1), None)
    return next((r for r in obs.rows if team_matches(intent.entity, r.team)), None)


def verify(intent: ResearchIntent, observations: list[Observation], current_season: str = "") -> Evidence:
    ev = Evidence(
        question_type="sports_standings",
        competition=intent.competition.name if intent.competition else "",
        season=intent.season or "",
        want=intent.want,
        entity=intent.entity,
        status="insufficient",
        current_season=current_season,
    )
    picked = [(o, r) for o in observations if (r := _pick(o, intent))]
    if not picked:
        if observations:
            ev.notes.append("tablolar bulundu ama istenen takım yok" if intent.entity else "tablolarda lider satırı yok")
        return ev
    # Freshest data first: more matches played means a later snapshot of the same season.
    freshest = max((r.played or 0) for _, r in picked)
    current = [(o, r) for o, r in picked if (r.played or 0) == freshest or r.played is None]
    older = [(o, r) for o, r in picked if r.played is not None and r.played < freshest]
    if intent.want == "leader" or not intent.entity:
        groups: dict[tuple[Any, ...], list[tuple[Observation, StandingRow]]] = {}
        for o, r in current:
            groups.setdefault((fold(r.team).split()[0], r.points), []).append((o, r))
    else:
        groups = {}
        for o, r in current:
            groups.setdefault((r.rank, r.points), []).append((o, r))
    ranked = sorted(
        groups.values(),
        key=lambda g: (len({o.domain for o, _ in g}), max(o.authority for o, _ in g), sum(o.origin != "snippet" for o, _ in g)),
        reverse=True,
    )
    best = ranked[0]
    lead_obs, lead_row = max(best, key=lambda x: (x[0].origin != "snippet", x[0].authority, x[1].played or 0))
    ev.row = lead_row
    ev.source_url = lead_obs.url
    ev.table = next((o.rows for o, _ in best if o.origin != "snippet"), lead_obs.rows)
    ev.agreeing = sorted({o.url for o, _ in best})
    domains = {o.domain for o, _ in best}
    if len(ranked) > 1:
        other = ranked[1][0]
        ev.status = "conflict"
        ev.notes.append(
            f"kaynaklar çelişiyor: {other[0].domain} → {other[1].describe()} ({other[1].team}); "
            f"daha fazla kaynağın/daha yetkili kaynağın verdiği değer seçildi"
        )
    else:
        ev.status = "verified" if len(domains) >= 2 else "single_source"
    if older:
        ev.notes.append(
            "bazı kaynaklar daha eski veri gösteriyor ("
            + ", ".join(f"{o.domain}: {r.played} maç" for o, r in older)
            + "); en güncel olan kullanıldı"
        )
    return ev


def render(ev: Evidence, source_ids: dict[str, int], domains: dict[str, str]) -> str:
    """Evidence block shown to the model, before the raw sources."""
    if not ev.found:
        reason = "; ".join(ev.notes) or "kaynaklarda istenen puan durumu satırı bulunamadı"
        return f"ÇIKARILAN KANIT: yok ({reason}). Sayı uydurma; kaynaklarda net değer yoksa bunu tek cümleyle söyle."
    row = ev.row
    assert row is not None
    cite = "".join(f"[{i}]" for i in sorted(source_ids[u] for u in ev.agreeing if u in source_ids))
    agreeing = ", ".join(domains.get(u, u) for u in ev.agreeing)
    status = {
        "verified": f"{len(ev.agreeing)} bağımsız kaynak aynı değeri veriyor ({agreeing})",
        "single_source": f"tek kaynak ({agreeing})",
        "conflict": f"kaynaklar çelişiyor; seçilen değer: {agreeing}",
    }[ev.status]
    who = row.team if ev.want == "leader" or not ev.entity else ev.entity
    lines = [
        "ÇIKARILAN KANIT (sayfalardaki puan tablolarından programla okundu; bu sorunun cevabı budur):",
        f"- {ev.competition} {ev.season} · {who}: {row.describe()} {cite}",
        f"- Doğrulama: {status}.",
    ]
    if ev.current_season and ev.season and ev.season < ev.current_season:
        finished = row.played is not None and ev.table and row.played >= max((r.played or 0) for r in ev.table)
        lines.append(
            f"- Sezon: {ev.season} GEÇMİŞ sezondur (şu anki sezon {ev.current_season}). Bu tablo o sezonun "
            + (
                "son (final) puan durumudur" + (f"; sezonun şampiyonu {row.team}." if row.rank == 1 else ".")
                if finished
                else "puan durumudur."
            )
        )
    lines += [f"- Not: {n}" for n in ev.notes]
    lines.append(f"- Alındığı an: {ev.retrieved_at[:16].replace('T', ' ')}")
    if ev.table:
        # Only the rows the question needs (top 3 + the team's neighbourhood): every extra row costs
        # prefill time before the first token. The whole table only when the user asked for it.
        rows = ev.table[:20] if ev.want == "table" else _focus_rows(ev.table, row)
        body = "\n".join(f"| {r.rank} | {r.team} | {_s(r.played)} | {_s(r.points)} |" for r in rows)
        first = source_ids.get(ev.source_url or "", 0)
        lines.append(
            f"Puan durumundan ilgili satırlar{f' [{first}]' if first else ''}:\n| Sıra | Takım | O | P |\n|---|---|---|---|\n{body}"
        )
    return "\n".join(lines)


def _focus_rows(table: list[StandingRow], row: StandingRow) -> list[StandingRow]:
    wanted = {1, 2, 3, row.rank - 1, row.rank, row.rank + 1}
    return [r for r in table if r.rank in wanted]


def _s(v: int | None) -> str:
    return "" if v is None else str(v)


def season_ok(intent: ResearchIntent, text: str) -> bool:
    """False when a text clearly talks about another season than the requested one."""
    if not intent.season:
        return True
    found = seasons_in(text)
    return not found or intent.season in found or bool(season_tokens(intent.season) & set(re.findall(r"\d{4}[-/–]\d{2,4}", text)))
