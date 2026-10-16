"""Fast intent routing: decide whether a message needs web research.

Three outcomes:
  - "search": clearly needs fresh information → research before answering (no extra LLM call needed).
  - "none":   clearly does not (greetings, small talk, math, explanations) → answer immediately, no tools.
  - "auto":   ambiguous → expose the `web_search` tool and let the model decide in its first pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Action = Literal["search", "none", "auto"]


@dataclass(frozen=True)
class RouteDecision:
    action: Action
    reason: str


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE | re.UNICODE)


EXPLICIT_SEARCH = _rx(
    r"\b(internet|web|google|online)\w*\b.*\b(ara|bak|araştır|bul|kontrol)\w*",
    r"\b(ara|araştır|bak)\w*\b.*\b(internet|web)\w*",
    r"\binternette\b",
    r"\bsearch (the )?(web|internet|online)\b",
    r"\b(look (it )?up|google it|search for)\b",
    r"\bkaynak(ları|lı|larla)?\b.*\b(göster|ver|bul)\w*",
)

# Topics that are inherently time-sensitive.
FRESH_TOPIC = _rx(
    r"\b(son dakika|en son|son çıkan)\b",
    r"\b(güncel|gündem|haber|haberler|haberleri|gelişme|gelişmeler|açıklandı|duyuruldu|yeni çıkan)\w*",
    r"\b(fiyat|fiyatı|kaç para|kur|kuru|dolar|euro|borsa|bitcoin|altın)\w*\b",
    r"\b(hava durumu|hava nasıl|hava nasil|sıcaklık)\b",
    r"\b(maç|skor|puan durumu|kim kazandı|seçim|sonuçları)\w*",
    r"\b(latest|breaking|news|price|stock|exchange rate|weather|score|who won|election)\b",
)
# Relative time words only count when the user is actually asking for information.
TIME_WORD = _rx(
    r"\b(bugün|bugünkü|dün|yarın|bu hafta|bu ay|bu yıl|geçen hafta|şu an|şu anda|şimdi)\b",
    r"\b(today|yesterday|tomorrow|this week|recent|current|currently|right now)\b",
    r"\b20[2-3]\d\b",
)
INFO_SEEKING = _rx(
    r"\?",
    r"\b(ne|neler|nedir|nasıl|kaç|kim|hangi|var mı|oldu mu|ne zaman|nerede|anlat|söyle|bilgi ver)\b",
    r"\b(what|which|who|how|when|where|is there|tell me)\b",
)


# First-person plans/states ("2031'de hangi telefonu alacağım?", "Yarın neyim vardı?"): a date word in a
# question about the user's own life is not a request for fresh web information. Fresh topics still win.
PERSONAL = _rx(
    r"\w+(acağım|eceğim|acak mıyım|ecek miyim|ıyorum|iyorum|uyorum|üyorum|ıyordum|iyordum|uyordum|üyordum|"
    r"mışım|mişim|muşum|müşüm|malıyım|meliyim)\b",
    r"\b(neyim|neyimiz|planım|planlarım|randevum|toplantım)\b",
)


def _time_sensitive(text: str) -> bool:
    if FRESH_TOPIC.search(text):
        return True
    return bool(TIME_WORD.search(text) and INFO_SEEKING.search(text) and not PERSONAL.search(text))

SMALL_TALK = _rx(
    r"^\s*(lazos[,!\s]*)?(merhaba|selam|hey|günaydın|iyi akşamlar|iyi geceler|naber|nasılsın|teşekkür(ler)?|"
    r"sağ ol|eyvallah|tamam|peki|anladım|güzel|harika|hello|hi|thanks|thank you|ok|okay)\b[\s\w!?.,']{0,40}$",
    r"^\s*(lazos[,!\s]*)?(sen kimsin|adın ne|ne yapabilirsin|kendini tanıt|who are you|what can you do)\b",
)

MATH = _rx(r"^[\s\d+\-*/().,%^=x×÷]+[?]?$", r"\b(kaç eder|hesapla|çarpı|bölü|artı|eksi|karekök)\b")

TIMELESS = _rx(
    r"^\s*(lazos[,!\s]*)?(bana\s+)?(.{0,80})\b(nedir|ne demek|nasıl çalışır|açıkla|anlat|tanımla|"
    r"farkı ne|örnek ver|kod yaz|yaz|çevir|özetle|explain|what is|how does|write|translate)\b",
)


def route(text: str, mode: str = "auto") -> RouteDecision:
    if mode == "off":
        return RouteDecision("none", "research disabled")
    t = text.strip()
    if mode == "always":
        return RouteDecision("search", "research forced by settings")
    if not t:
        return RouteDecision("none", "empty")
    if EXPLICIT_SEARCH.search(t):
        return RouteDecision("search", "explicit request")
    if SMALL_TALK.search(t):
        return RouteDecision("none", "small talk")
    if MATH.search(t):
        return RouteDecision("none", "math")
    if _time_sensitive(t):
        return RouteDecision("search", "time-sensitive")
    if TIMELESS.search(t) and len(t) < 200:
        return RouteDecision("none", "general explanation")
    return RouteDecision("auto", "model decides")


_MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
_STRIP = _rx(
    r"\blazos\b[,!.:]?",
    r"\b(lütfen|bana|benim için|bir|şunu|bunu)\b",
    r"\b(internette|internetten|webde|web'de|google'da|online)\b",
    r"\b(ara(r mısın|yabilir misin|yın|)|araştır(ır mısın|abilir misin|)|bak(ar mısın|abilir misin)|bul(ur musun|))\b",
    r"\b(search (the )?(web|internet) (for)?|look up|please|can you|could you)\b",
)
_PUNCT = re.compile(r"[?!.,;:]+(?=\s|$)")
_RELATIVE_DAY = _rx(r"\b(bugün|bugünkü|today|şu an|şu anda|şimdi|right now)\b")


def build_search_query(text: str, now: datetime | None = None) -> str:
    """Turn a conversational request into a search query without an extra LLM round-trip.

    Keeps the user's wording (search engines handle natural questions well), removes the assistant's
    name and command phrases, and anchors relative dates to the actual date.
    """
    now = now or datetime.now()
    query = _STRIP.sub(" ", text)
    query = _PUNCT.sub(" ", query)  # keeps decimals like "3.5"
    if _RELATIVE_DAY.search(query):
        query = _RELATIVE_DAY.sub(" ", query) + f" {now.day} {_MONTHS_TR[now.month - 1]} {now.year}"
    elif _time_sensitive(text) and not re.search(r"\b20[2-3]\d\b", query):
        query += f" {now.year}"
    query = re.sub(r"\s+", " ", query).strip()
    return query[:200] if len(query) >= 2 else text.strip()[:200]
