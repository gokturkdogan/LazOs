"""Deterministic learning policy: what feedback means, what may be learned, and what is never used."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.memory.intent import contains_secret, is_identity_fact
from app.memory.text import fold

REASONS: dict[str, str] = {
    "incorrect": "Yanlış bilgi",
    "unnatural_turkish": "Doğal olmayan Türkçe",
    "too_long": "Çok uzun",
    "too_short": "Çok kısa",
    "misunderstood": "İsteği yanlış anladı",
    "lost_context": "Konuşma bağlamını kaybetti",
    "wrong_tone": "Yanlış üslup",
    "unnecessary_tool": "Gereksiz araç kullanımı",
    "other": "Diğer",
}


@dataclass(frozen=True)
class PreferenceTemplate:
    signal: str
    proposal: str
    category: str
    opposite: str | None = None


# Repeated reasons that describe a *style preference*. Accuracy problems (incorrect, misunderstood,
# lost_context) are quality issues, never preferences, so they don't produce suggestions.
TEMPLATES: dict[str, PreferenceTemplate] = {
    "too_long": PreferenceTemplate("too_long", "Kullanıcı daha kısa ve öz cevaplar tercih ediyor.", "communication", "too_short"),
    "too_short": PreferenceTemplate("too_short", "Kullanıcı daha ayrıntılı cevaplar tercih ediyor.", "communication", "too_long"),
    "unnatural_turkish": PreferenceTemplate(
        "unnatural_turkish", "Kullanıcı daha doğal, günlük konuşma Türkçesi tercih ediyor.", "communication"
    ),
    "wrong_tone": PreferenceTemplate("wrong_tone", "Kullanıcı daha sade ve ölçülü bir üslup tercih ediyor.", "communication"),
    "unnecessary_tool": PreferenceTemplate(
        "unnecessary_tool", "Kullanıcı gereksiz internet aramasından hoşlanmıyor; yalnızca gerçekten gerektiğinde arama yapılmalı.", "communication"
    ),
}
SUGGESTION_THRESHOLD = 3  # independent pieces of evidence before a preference is *suggested*
STT_TERM_THRESHOLD = 2  # corrections containing the same new term before a vocabulary hint is suggested

# Concept groups used to detect contradicting preferences (in existing memories too).
_OPPOSING = [
    (re.compile(r"\b(kisa|oz|ozet|az kelime)\w*"), re.compile(r"\b(ayrintili|detayli|uzun|kapsamli)\w*")),
    (re.compile(r"\b(resmi|ciddi|profesyonel)\w*"), re.compile(r"\b(samimi|esprili|rahat|eglenceli)\w*")),
]

# Claims that can go stale: versions/decimals, years, amounts with units, and time-sensitive words.
# Plain small numbers ("İki seçenek var: 1) …") are style, not facts.
_FACTUAL = re.compile(
    r"\d+[.,]\d+|\b(19|20)\d{2}\b|\d+\s*(tl|lira|dolar|euro|usd|\$|%|gb|mb|ms|km|kg)\b|\bv\d"
    r"|\b(surum|versiyon|version|artik|fiyat|ucret|tarih|guncel|yeni cikan|en son|release|price|dolar|euro)\w*"
    r"|\bcikti\b"  # "çıktı" = was released; NOT "çıktısını" (= its output), which is ordinary debugging advice
)
# A correction that tries to rename LAZOS or its creator ("Ben Max", "Beni Ali yaptı") must never become an example.
_IDENTITY_CLAIM = re.compile(r"\b(benim adim|adim|ben)\s+(?!lazos\b)[a-z]{2,}\b.*\b(asistan|yapay zeka)|\b(yaraticim|beni .* (yapti|gelistirdi|yaratti))")
_ROLE_MARKERS = re.compile(r"(<\|im_(start|end)\|>|</?tool_\w+>|^\s*(system|sistem|assistant|user)\s*:|#{2,}\s*(talimat|instruction))", re.IGNORECASE | re.MULTILINE)
_INSTRUCTION = re.compile(
    r"\b(onceki|tum|butun)\s+(talimat|kural)\w*\s+(yok say|unut|gormezden gel)|\bignore (all|previous|the above)\b|\bsen artik\b",
)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE = re.compile(r"(?<!\d)(\+?90[ -]?)?0?5\d{2}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?!\d)")
_TCKN = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")
_TOKEN = re.compile(r"\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}|xox[bp]-[\w-]{10,})\b")
_ADDRESS = re.compile(r"\b(mahallesi|mah\.|sokak|sok\.|cadde|cad\.|apartman|daire|no:)\b", re.IGNORECASE)


def classify_correction(preferred: str, reasons: list[str] | None = None) -> str:
    """"factual" corrections carry claims that may go stale; they are never reused as examples."""
    if "incorrect" in (reasons or []) or _FACTUAL.search(fold(preferred)):
        return "factual"
    return "style"


def blocked_reason(text: str) -> str | None:
    """Why a text may never be used as an example or exported (None when it is safe)."""
    folded = fold(text)
    if contains_secret(text) or _TOKEN.search(text):
        return "secret"
    if is_identity_fact(text) or _IDENTITY_CLAIM.search(folded):
        return "identity"
    if _ROLE_MARKERS.search(text) or _INSTRUCTION.search(folded):
        return "instruction"
    return None


def pii_warnings(text: str) -> list[str]:
    warnings = []
    if _EMAIL.search(text):
        warnings.append("e-posta adresi")
    if _PHONE.search(text):
        warnings.append("telefon numarası")
    if _TCKN.search(text):
        warnings.append("kimlik numarası olabilir")
    if _ADDRESS.search(text):
        warnings.append("adres bilgisi")
    return warnings


def sanitize_example(text: str, limit: int = 360) -> str:
    """Examples are data: collapse whitespace, strip role markers, cap length."""
    previous = None
    while previous != text:  # removing one marker can expose another at a line start
        previous, text = text, _ROLE_MARKERS.sub(" ", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def conflicts(a: str, b: str) -> bool:
    """True when two preference statements point in opposite directions (e.g. short vs detailed)."""
    fa, fb = fold(a), fold(b)
    for left, right in _OPPOSING:
        if (left.search(fa) and right.search(fb)) or (right.search(fa) and left.search(fb)):
            return True
    return False


_WORD = re.compile(r"[\w'’.-]+", re.UNICODE)


def new_terms(original: str, corrected: str) -> list[str]:
    """Words that appear in the corrected transcript but not in Whisper's original (vocabulary candidates)."""
    before = {fold(w).strip(".'’") for w in _WORD.findall(original)}
    out: list[str] = []
    for word in _WORD.findall(corrected):
        clean = word.strip(".,'’")
        base = re.split(r"['’]", clean)[0]  # "backend'i" → "backend"
        if len(base) < 3 or fold(base) in before or base.isdigit():
            continue
        if base not in out:
            out.append(base)
    return out
