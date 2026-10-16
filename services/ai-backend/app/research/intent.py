"""What kind of web research a request needs (deterministic, no LLM call).

The router decides *whether* to search; this module says *what* is being asked, so the pipeline can
build a targeted query, rank sources, and extract the answer deterministically where possible:

  standings  "Galatasaray ligde kaçıncı sırada?"   → competition, season, entity, wanted value
  weather    "İstanbul'da hava nasıl?"
  exchange   "Dolar kaç TL?"
  version    "Python'un en son sürümü?"
  news       "Yapay zekâ dünyasında bu hafta neler oldu?"
  current    "Apple'ın şu anki CEO'su kim?"      (a fact that changes over time)
  general    everything else
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

IntentKind = Literal["standings", "weather", "exchange", "version", "news", "current", "general"]
Want = Literal["rank", "points", "leader", "table", "value"]

_TR = str.maketrans("İIıŞşĞğÜüÖöÇçÂâÎîÛû’", "iiissgguuoocçaaiiuu'")


def fold(text: str) -> str:
    """Lower-case, Turkish-aware, accent-free ("Beşiktaş'ın" → "besiktas'in")."""
    return text.translate(_TR).replace("ç", "c").lower()


@dataclass(frozen=True)
class Competition:
    key: str
    name: str  # display / query name
    patterns: tuple[str, ...]  # folded regexes


COMPETITIONS: tuple[Competition, ...] = (
    Competition("champions_league", "UEFA Şampiyonlar Ligi", (r"sampiyonlar lig", r"champions league", r"\bucl\b")),
    Competition("europa_league", "UEFA Avrupa Ligi", (r"avrupa lig", r"europa league")),
    Competition("conference_league", "UEFA Konferans Ligi", (r"konferans lig", r"conference league")),
    Competition("first_league", "Trendyol 1. Lig", (r"\b1\. ?lig", r"birinci lig", r"tff 1")),
    Competition("premier_league", "Premier Lig", (r"premier li", r"ingiltere (premier )?lig")),
    Competition("la_liga", "La Liga", (r"la liga", r"ispanya lig")),
    Competition("serie_a", "Serie A", (r"serie a", r"italya lig")),
    Competition("bundesliga", "Bundesliga", (r"bundesliga", r"almanya lig")),
    Competition("super_lig", "Trendyol Süper Lig", (r"super ?lig", r"superlig")),
)
SUPER_LIG = COMPETITIONS[-1]

# Club names help find the entity and imply the domestic league; they are not used for any answer.
CLUBS = (
    "Galatasaray",
    "Fenerbahçe",
    "Beşiktaş",
    "Trabzonspor",
    "Başakşehir",
    "Samsunspor",
    "Göztepe",
    "Eyüpspor",
    "Kasımpaşa",
    "Kayserispor",
    "Konyaspor",
    "Antalyaspor",
    "Alanyaspor",
    "Rizespor",
    "Gaziantep",
    "Kocaelispor",
    "Gençlerbirliği",
    "Karagümrük",
    "Amedspor",
    "Amed",
    "Erzurumspor",
    "Sivasspor",
    "Bodrumspor",
    "Hatayspor",
    "Adana Demirspor",
    "Pendikspor",
    "Çorum",
    "Bandırmaspor",
    "Manisa",
    "Sakaryaspor",
    "Ankaragücü",
    "Bursaspor",
)
_CLUB_RX = re.compile(r"\b(" + "|".join(re.escape(fold(c)) for c in CLUBS) + r")\w*")

_SPORT_CONTEXT = re.compile(r"\b(lig\w*|sezon\w*|takim\w*|puan\w*|mac\w*|futbol|basket\w*|voleybol)")
_STANDINGS = re.compile(
    r"kacinci (sira|oldu|bitirdi)|kacinci(da|dayiz|yiz)?\b|puan (durum|tablo|cetvel)|siralama|siralam|kac puan"
    r"|\blider\w*|zirve\w*|sampiyon\w*|kim kazandi|kazanan kim|ilk sirada|son sirada|kume dus|standings|league table"
)
_LEADER = re.compile(r"\blider\w*|zirve\w*|sampiyon\w*|kim kazandi|kazanan kim|ilk sirada kim|birinci kim")
_POINTS = re.compile(r"kac puan|puani (kac|ne)|puanı")
_PREV_SEASON = re.compile(r"gecen sezon|onceki sezon|last season|gecen yil(ki)? lig")
_SEASON_TEXT = re.compile(r"\b(20\d{2})\s*[-/–]\s*(20)?(\d{2})\b")

_WEATHER = re.compile(r"hava (durumu|nasil|nasildir|kac derece)|hava sicakli|yagmur yag|kar yag|sicaklik kac|kac derece|weather")
_EXCHANGE = re.compile(
    r"\b(dolar|euro|avro|sterlin|frank|yen|altin|gram altin|ceyrek|bitcoin|btc|ethereum|bist|borsa)\b.*\b(kac|ne kadar|kur|fiyat|deger|tl)"
    r"|\b(doviz|kur(u|lari)?)\b.*\b(kac|ne kadar|nedir|bugun)|exchange rate|\b(usd|eur)\s*/?\s*(try|tl)\b"
)
_VERSION = re.compile(
    r"(en (son|yeni|guncel)|guncel|son|latest|stable|kararli|lts)\b.{0,30}\b(surum|versiyon|version|release|lts)\w*"
    r"|\b(surum|versiyon|version)\w*\b.{0,20}\b(kac|hangisi|ne|nedir)"
)
_NEWS = re.compile(r"\b(haber\w*|gundem\w*|son dakika|neler oldu|ne oldu|gelismeler?|news)\b")
_CURRENT_ROLE = re.compile(
    r"\b(ceo|genel muduru?|yonetim kurulu baskani|baskani|bakani|cumhurbaskani|basbakan|teknik direktor\w*|hocasi|sahibi)\b"
    r".{0,30}\bkim\b|\bkim\b.{0,30}\b(ceo|baskan|bakan|teknik direktor)"
)
_CITY = re.compile(r"\b([A-ZÇĞİÖŞÜ][a-zçğıöşü]+)(?:'?(?:da|de|ta|te|daki|deki|nin|nın|un|ün|in|ın))?\b")
_NOT_CITY = {"Bugün", "Yarın", "Hava", "Şu", "Peki", "Lazos", "LAZOS", "Ya", "Bu", "Hafta", "Sonu"}


@dataclass(frozen=True)
class ResearchIntent:
    kind: IntentKind
    want: Want = "value"
    entity: str | None = None
    competition: Competition | None = None
    season: str | None = None  # "2026-27"

    def describe(self) -> str:
        parts = [self.kind, self.want]
        if self.entity:
            parts.append(self.entity)
        if self.competition:
            parts.append(self.competition.name)
        if self.season:
            parts.append(self.season)
        return " · ".join(parts)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "want": self.want,
            "entity": self.entity,
            "competition": self.competition.name if self.competition else None,
            "season": self.season,
        }

    @property
    def structured(self) -> bool:
        """The answer is a value we can extract and verify deterministically."""
        return self.kind == "standings"


def football_season(now: datetime, offset: int = 0) -> str:
    """European football seasons start in July/August: 16 Oct 2026 → "2026-27"."""
    start = (now.year if now.month >= 7 else now.year - 1) + offset
    return f"{start}-{(start + 1) % 100:02d}"


def season_tokens(season: str) -> set[str]:
    """Spellings of a season used on pages: 2026-27, 2026/27, 2026-2027, 2026/2027."""
    start = int(season[:4])
    end = start + 1
    return {f"{start}-{end % 100:02d}", f"{start}/{end % 100:02d}", f"{start}-{end}", f"{start}/{end}", f"{start}–{end % 100:02d}"}


def seasons_in(text: str) -> set[str]:
    """Normalised seasons mentioned in a text ("2025/2026" → "2025-26")."""
    out = set()
    for m in _SEASON_TEXT.finditer(text):
        start = int(m.group(1))
        end = int(m.group(3))
        if end == (start + 1) % 100:
            out.add(f"{start}-{end:02d}")
    return out


def competition_in(text: str) -> Competition | None:
    f = fold(text)
    for comp in COMPETITIONS:
        if any(re.search(p, f) for p in comp.patterns):
            return comp
    return None


def find_club(text: str) -> str | None:
    m = _CLUB_RX.search(fold(text))
    if not m:
        return None
    key = m.group(1)
    return next(c for c in CLUBS if fold(c) == key)


def _city(text: str) -> str | None:
    return next((m.group(1) for m in _CITY.finditer(text) if m.group(1) not in _NOT_CITY), None)


def classify(text: str, now: datetime | None = None) -> ResearchIntent:
    now = now or datetime.now()
    f = fold(text)
    club = find_club(text)
    comp = competition_in(text)
    if _STANDINGS.search(f) and (club or comp or _SPORT_CONTEXT.search(f)):
        offset = -1 if _PREV_SEASON.search(f) else 0
        mentioned = seasons_in(text)
        season = sorted(mentioned)[0] if mentioned else football_season(now, offset)
        if _LEADER.search(f) and not club:
            want: Want = "leader"
        elif _POINTS.search(f):
            want = "points"
        elif club:
            want = "rank"
        else:
            want = "table"
        return ResearchIntent("standings", want, club, comp or SUPER_LIG, season)
    if _WEATHER.search(f):
        return ResearchIntent("weather", entity=_city(text))
    if _EXCHANGE.search(f):
        return ResearchIntent("exchange")
    if _VERSION.search(f):
        product = re.match(r"\s*(?:lazos[,\s]+)?([A-Za-zÇĞİÖŞÜçğıöşü][\w.+#-]*)(?:'|’|\s)", text, re.IGNORECASE)
        return ResearchIntent("version", entity=product.group(1) if product else None)
    if _CURRENT_ROLE.search(f):
        return ResearchIntent("current")
    if _NEWS.search(f):
        return ResearchIntent("news")
    return ResearchIntent("general")


_FOLLOWUP = re.compile(r"^\s*(peki|ya|ve|bir de|o zaman|ayrica|what about|and)\b", re.IGNORECASE)


def followup_intent(text: str, previous: ResearchIntent | None, now: datetime | None = None) -> ResearchIntent | None:
    """An elliptical follow-up ("Peki Fenerbahçe?") inherits the previous research intent."""
    if previous is None or previous.kind == "general" or len(text.split()) > 6:
        return None
    if previous.kind == "standings":
        club = find_club(text)
        if _LEADER.search(fold(text)) and not club:
            return replace(previous, entity=None, want="leader")
        if club or _FOLLOWUP.search(text):
            own = classify(text, now)
            want = own.want if own.kind == "standings" else ("rank" if club else previous.want)
            return replace(previous, entity=club or previous.entity, want=want)
        return None
    if _FOLLOWUP.search(text):
        return previous
    return None
