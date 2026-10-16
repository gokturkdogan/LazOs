"""Cheap, deterministic signals that decide when the (expensive) memory model runs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.memory.text import fold

_F = re.IGNORECASE | re.UNICODE


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), _F)


# Patterns are matched against fold(text): lower-case ASCII-like Turkish (ş→s, ı→i, ...).
REMEMBER = _rx(
    r"\b(bunu|sunu|bunlari|onu)?\s*(hatirla|aklinda tut|aklina yaz|unutma|not al|not et|kaydet|hafizana (al|ekle|kaydet))\b(?!\s*(mi|misin|musun)\b)",
    r"\b(remember|don'?t forget|keep in mind|note that)\b",
)
PERSISTENT_INSTRUCTION = _rx(
    # imperative verb forms only, so "artık Rust kullanıyorum" (a statement) is not an instruction
    r"\b(bundan (boyle|sonra)|artik( bana| benimle)?|her zaman|hep)\b.*\b(ver|yaz|konus|cevapla|yanitla|soyle|kullan|hitap et|ol|yap|etme|yapma|kullanma)(in|iniz)?\b",
    r"\b(from now on|always|never again)\b",
)
FORGET = _rx(
    r"\b(unut|unutur musun|unutabilir misin|hafizandan (sil|cikar)|kaydi sil|kaydini sil|bunu sil)\b",
    r"\b(forget( that| it)?|delete (that|this) memory)\b",
)
HYPOTHETICAL = _rx(
    r"\b(diyelim ki|farz et|farzedelim|varsayalim|hayal et|olsaydi|olsaydim|olsam|rol yap|senaryo|mesela|ornegin|sanki)\b",
    r"\b(imagine|suppose|hypothetically|let'?s say|pretend|what if|role ?play)\b",
)
EPISODIC = _rx(
    r"\b(gecen|onceki|son|daha once|onceden|evvelki|dun|gecenlerde|ilk)\b.{0,40}\b(konusma|sohbet|bahs|konus|soyle|sor|anlat|dedi|demis)\w*",
    r"\b(hatirliyor musun|hatirlar misin|ne konusmustuk|ne demistim|ne soylemistim|bahsetmistik|bahsetmistim|konusmustuk|soylemistim|sormustum|demistin|soylemistin|anlatmistim)\b",
    r"\b(last time|we talked|did i (tell|mention)|remember when|previous conversation)\b",
)
# First-person statements that may contain durable facts about the user.
LEARNABLE = _rx(
    r"\b(ben|benim|bana|beni|bende|kendim|adim|ismim|yasim|meslegim|isim)\b",
    r"\w+(iyorum|uyorum|iyoruz|uyoruz)\b",
    r"\b(tercih|genellikle|genelde|seviyorum|sevmem|sevmiyorum|nefret|projem|projemiz|hobim|karar verdim|karar verdik|planliyorum)\w*",
    r"\b(i am|i'm|my|i use|i prefer|i like|i love|i hate|i work|i live)\b",
)
SENSITIVE = _rx(
    r"\b(hastalig|teshis|depresyon|kanser|diyabet|hamile|terapi|psikiyatr|ilac(im|lar|larim)|ameliyat)\w*",
    r"\b(dinim|dini inanc|mezheb|ibadet|siyasi gorus|oy ver(dim|ecegim)|cinsel yonelim)\w*",
    r"\b(maasim|borcum|kredi kart|iban|sifre|parola|password|tc kimlik|kimlik numara|pin kod)\w*",
)
SECRET = re.compile(r"\b(\d[ -]?){13,19}\b|\bTR\d{2}[0-9 ]{10,30}\b|\b(sifre|parola|password|pin)\w*\s*(:|=|\bis\b|\bsu\b)\s*\S+", _F)


def is_remember(text: str) -> bool:
    return bool(REMEMBER.search(fold(text)) or PERSISTENT_INSTRUCTION.search(fold(text)))


def is_forget(text: str) -> bool:
    return bool(FORGET.search(fold(text)))


def is_hypothetical(text: str) -> bool:
    return bool(HYPOTHETICAL.search(fold(text)))


# "az önce", "demin", "bu konuşmada" point at the *current* conversation, which is already in context.
CURRENT_CONVERSATION = _rx(r"\b(az once|biraz once|demin|az evvel|bu konusma|bu sohbet|simdi|just now|earlier in this)\w*")
IDENTITY_FACT = _rx(
    r"\blazos\S*\s+((adi|ismi|adinin|isminin)\b|(yaratici|kimlig|gelistirici)\w*)",
    r"\b(senin|asistanin)\s+(adi|ismi|yaratici)\w*",
    r"\b(yaraticisi|gelistiricisi)\w*\b.*\blazos|\blazos\S*\s+.*\b(yaraticisi|gelistiricisi)",
)
# First-person questions about the user's own things ("projem", "tercihlerim") may use category hints.
OWN_THINGS = _rx(
    r"\b(projem|projelerim|projemiz|uygulamam|uygulamalarim|tercihim|tercihlerim|ilgi alanlarim|hobim|hobilerim|kararim|kararlarim|my projects?|my preferences?)\w*"
)


def is_episodic(text: str) -> bool:
    folded = fold(text)
    return bool(EPISODIC.search(folded)) and not CURRENT_CONVERSATION.search(folded)


def is_identity_fact(text: str) -> bool:
    return bool(IDENTITY_FACT.search(fold(text)))


def asks_about_own_things(text: str) -> bool:
    return bool(OWN_THINGS.search(fold(text)))


def is_learnable(text: str) -> bool:
    t = text.strip()
    return len(t) >= 10 and bool(LEARNABLE.search(fold(t))) and not is_hypothetical(t)


def is_sensitive(text: str) -> bool:
    return bool(SENSITIVE.search(fold(text)))


def contains_secret(text: str) -> bool:
    return bool(SECRET.search(fold(text)))


@dataclass(frozen=True)
class TimeWindow:
    since: float | None
    until: float | None
    label: str


def time_window(text: str, now: datetime | None = None) -> TimeWindow | None:
    """Map relative time expressions in a question to a search window."""
    now = now or datetime.now()
    t = fold(text)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if re.search(r"\b(bugun|today)\b", t):
        return TimeWindow(day_start.timestamp(), None, "bugün")
    if re.search(r"\b(dun|yesterday)\b", t):
        return TimeWindow((day_start - timedelta(days=1)).timestamp(), day_start.timestamp(), "dün")
    if re.search(r"\b(gecen hafta|last week)\b", t):
        return TimeWindow((day_start - timedelta(days=14)).timestamp(), day_start.timestamp(), "geçen hafta")
    if re.search(r"\b(bu hafta|this week)\b", t):
        return TimeWindow((day_start - timedelta(days=7)).timestamp(), None, "bu hafta")
    if re.search(r"\b(gecen ay|last month)\b", t):
        return TimeWindow((day_start - timedelta(days=62)).timestamp(), (day_start - timedelta(days=14)).timestamp(), "geçen ay")
    return None


def wants_latest_conversation(text: str) -> bool:
    return bool(re.search(r"\b(gecen|son|onceki|evvelki)\s+(konusma|sohbet)|\blast (conversation|time)\b", fold(text)))
