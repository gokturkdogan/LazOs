"""Turkish-aware text normalisation for local full-text retrieval (SQLite FTS5).

SQLite's tokenizer does not know Turkish casing (I/ı, İ/i) or suffixes, so both indexed text and
queries are folded to an ASCII-like form here, and queries use short prefixes as a light stemmer
("projeden" → "proje*", "kullanıyorum" → "kulla*").
"""

from __future__ import annotations

import re

_TR_MAP = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ç": "c", "ö": "o", "ü": "u", "â": "a", "î": "i", "û": "u"})
_WORD = re.compile(r"[0-9a-z]+")

STOPWORDS = {
    # Turkish function words
    "ve", "veya", "ile", "bir", "bu", "su", "o", "ne", "neydi", "nedir", "neler", "mi", "mu", "da", "de", "ki",
    "icin", "gibi", "cok", "daha", "en", "ben", "sen", "biz", "siz", "bana", "beni", "benim", "sana", "seni",
    "senin", "bize", "hangi", "nasil", "neden", "niye", "ama", "fakat", "ya", "hem", "var", "yok", "olan",
    "olarak", "kadar", "sonra", "once", "simdi", "artik", "hep", "her", "zaman", "lutfen", "tamam", "evet",
    "hayir", "lazos", "kullanici", "acaba", "bunu", "sunu", "onu", "bunlar", "seyi", "sey",
    # time words used in episodic questions
    "gecen", "hafta", "haftaki", "ay", "gun", "dun", "bugun", "yarin", "gecenlerde", "son", "onceki", "evvelki",
    # English
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "i", "you", "my", "me", "we", "what",
    "which", "last", "time", "week", "about", "did", "do", "that", "this", "it",
}

# Verb stems that describe *talking about* something rather than the topic itself.
_META_PREFIXES = ("konus", "sohbe", "bahse", "soyle", "hatir", "demis", "dedi", "sormu", "sordu", "anlatm", "unut", "talk", "said", "tell", "remem")


def fold(text: str) -> str:
    """Lower-case with Turkish rules, then strip Turkish diacritics."""
    text = text.replace("İ", "i").replace("I", "ı").lower()
    return text.translate(_TR_MAP)


def tokens(text: str) -> list[str]:
    return _WORD.findall(fold(text))


def index_body(text: str) -> str:
    """The text stored in FTS tables (folded; the generic subject word is dropped)."""
    return " ".join(t for t in tokens(text) if t != "kullanici")


def content_terms(text: str) -> list[str]:
    """Meaningful query terms: no stopwords, no meta-verbs, deduplicated, original order."""
    seen: list[str] = []
    for tok in tokens(text):
        if tok in STOPWORDS or len(tok) < 3 or (tok.isdigit() and len(tok) < 4):
            continue
        if tok.startswith(_META_PREFIXES):
            continue
        if tok not in seen:
            seen.append(tok)
    return seen


def stem(token: str) -> str:
    return token if len(token) <= 5 else token[:5]


def fts_query(terms: list[str]) -> str | None:
    stems: list[str] = []
    for t in terms:
        s = stem(t)
        if s not in stems:
            stems.append(s)
    if not stems:
        return None
    return " OR ".join(f'"{s}"*' for s in stems[:12])


_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-ZÇĞİÖŞÜ][\wçğıöşü'’.-]{2,}")


def distinctive_overlap(query: str, content: str) -> bool:
    """True when a memory is genuinely about the query: at least two shared meaningful stems, or a shared
    proper noun ("Atlas", "Karabaş", "React"). A single generic word ("uygulama") is not enough."""
    q = {stem(t) for t in content_terms(query)}
    c = {stem(t) for t in content_terms(content)}
    shared = q & c
    if len(shared) >= 2:
        return True
    proper = {stem(t) for word in _PROPER.findall(query) for t in tokens(word) if t not in STOPWORDS}
    return bool(shared & proper)


def similarity(a: str, b: str) -> float:
    """Jaccard similarity over stemmed tokens — used to detect duplicate memories."""
    sa = {stem(t) for t in tokens(a) if t not in STOPWORDS and t != "kullanici"}
    sb = {stem(t) for t in tokens(b) if t not in STOPWORDS and t != "kullanici"}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
