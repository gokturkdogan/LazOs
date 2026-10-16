"""Pronunciation layer: rewrites text *only on its way to the speech engine*.

The displayed answer, conversation history and memories are never modified. Rules are applied to
the already-speakable sentence (markdown, code blocks and URLs removed by `speech.text`), right
after the engine has been chosen, because the same respelling sounds different on different voices.

Built-in respellings were chosen empirically (2026-10-16): each candidate was synthesised inside a
Turkish sentence with the installed voice and transcribed with Whisper (Turkish, no vocabulary
hints); a candidate "passes" when Whisper recovers the original term. Terms marked `verified=False`
had no passing candidate and use the conventional Turkish pronunciation — they need a human ear.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

import aiosqlite

ENGINES = ("piper", "macos")


@dataclass(frozen=True)
class Rule:
    term: str
    replacement: str
    engine: str = "all"  # "all" | "piper" | "macos"
    case_sensitive: bool = False
    enabled: bool = True
    id: str = ""
    builtin: bool = False
    verified: bool = True

    def applies_to(self, engine: str) -> bool:
        return self.enabled and self.engine in ("all", engine)

    def to_dict(self) -> dict:
        return asdict(self)


def _b(term: str, replacement: str, engine: str = "all", *, verified: bool = True, case_sensitive: bool = False) -> Rule:
    return Rule(term, replacement, engine, case_sensitive, True, f"builtin:{engine}:{term}", True, verified)


BUILTIN_RULES: tuple[Rule, ...] = (
    # ── technical terms (Whisper round-trip results noted per engine) ─────────
    _b("backend", "bek end"),  # piper ✓ (raw → "Bağcıkent"); macos: no candidate recovered → conventional
    _b("JavaScript", "cava skript"),  # piper ✓ macos ✓
    _b("TypeScript", "tayp skript"),  # piper ✓ (raw → "Tipes Cipta") macos ✓ (raw → "Tayyip Skript")
    _b("GitHub", "git hab"),  # piper ✓ macos ✓ (raw → "Gitvurdu")
    _b("Docker", "doker", verified=False),  # no candidate recovered on either voice; conventional Turkish
    _b("Python", "payton"),  # piper ✓ macos ✓
    _b("FastAPI", "fast ey pi ay", "piper"),  # piper ✓ (raw → "Fast AP")
    _b("FastAPI", "fest ey pi ay", "macos", verified=False),  # closest: "Fest API"
    _b("API", "ey pi ay", case_sensitive=True),  # piper ✓ (raw → "AP") macos ✓
    _b("SQL", "es kü el", case_sensitive=True),  # piper ✓ (raw → "Sekyule") macos ✓
    _b("JSON", "ceysın", verified=False),  # heard as "Jason" — i.e. the English pronunciation
    _b("React", "riekt"),  # piper ✓ macos ✓ (raw → "Reacit")
    _b("Node.js", "nod cey es"),  # piper ✓ macos ✓
    _b("npm", "en pi em"),  # piper ✓ macos ✓
    _b("framework", "freymvörk", "piper"),  # piper ✓ (raw → "From the Org"); macos reads raw correctly
    _b("deployment", "diployment", "piper", verified=False),  # closest on piper; macos reads raw correctly
    _b("repository", "repozitori", "piper"),  # piper ✓; macos reads raw correctly
    _b("database", "deytabeys"),  # piper ✓ macos ✓ (raw → "de Tabase")
    _b("server", "sörvır", "piper"),  # piper ✓
    _b("server", "sörver", "macos", verified=False),  # raw → "Sörlür"
    _b("localhost", "lokal host"),  # piper ✓ (raw → "Lojal Host") macos ✓
    _b("macOS", "mek o es", verified=False),
    _b("MacBook", "mekbuk", verified=False),
    _b("Mac", "Mek", verified=False, case_sensitive=True),  # raw piper reads "Maç"; conventional Turkish
    # ── abbreviations ─────────────────────────────────────────────────────────
    _b("vb.", "ve benzeri"),
    _b("vs.", "vesaire"),
    _b("örn.", "örneğin"),
    _b("bkz.", "bakınız"),
    _b("TL", "lira", case_sensitive=True),
)

# Spans that must never be rewritten: URLs, e-mail addresses, file paths, commands/assignments.
_PROTECTED = re.compile(r"https?://\S+|www\.\S+|\b[\w.+-]+@[\w-]+\.[\w.]+|(?:~|\.{1,2})?/[\w./-]+|\b\w+[\\/][\w./\\-]+|\S*=\S+|\S+\(\)")
_REPEATED = re.compile(r"([!?.,;:])\1+")
_MIXED_END = re.compile(r"[!?]{2,}")


def _compile(rules: Iterable[Rule]) -> tuple[re.Pattern[str] | None, dict[str, str], dict[str, str]]:
    sensitive: dict[str, str] = {}
    insensitive: dict[str, str] = {}
    for rule in rules:
        (sensitive if rule.case_sensitive else insensitive)[rule.term if rule.case_sensitive else rule.term.casefold()] = rule.replacement
    terms = sorted(set(sensitive) | set(insensitive), key=len, reverse=True)  # multiword/longer first
    if not terms:
        return None, sensitive, insensitive
    alternation = "|".join(re.escape(t) for t in terms)
    # Word boundaries that understand Turkish letters; a trailing apostrophe suffix ("backend'i") is kept.
    pattern = re.compile(rf"(?<![\w.])({alternation})(?![\w])", re.IGNORECASE)
    return pattern, sensitive, insensitive


class Pronouncer:
    """Applies the active rule set for one engine. Rule sets are compiled once per change."""

    def __init__(self, rules: Iterable[Rule] = BUILTIN_RULES) -> None:
        self._compiled: dict[str, tuple[re.Pattern[str] | None, dict[str, str], dict[str, str]]] = {}
        self.set_rules(rules)

    def set_rules(self, rules: Iterable[Rule]) -> None:
        rules = list(rules)
        # A custom rule overrides every built-in for the same term (case-insensitively) and engine scope.
        custom_terms = {(r.term.casefold(), r.engine) for r in rules if not r.builtin}
        effective = [
            r
            for r in rules
            if not r.builtin or not ((r.term.casefold(), r.engine) in custom_terms or (r.term.casefold(), "all") in custom_terms)
        ]
        self._compiled = {engine: _compile(r for r in effective if r.applies_to(engine)) for engine in ENGINES}

    @staticmethod
    def cleanup(text: str) -> str:
        """Collapse repeated punctuation ("!!!" → "!", "?!" → "?"); always applied to speech."""
        return _REPEATED.sub(r"\1", _MIXED_END.sub(lambda m: m.group(0)[0], text))

    def apply(self, text: str, engine: str) -> str:
        pattern, sensitive, insensitive = self._compiled.get(engine, (None, {}, {}))
        text = self.cleanup(text)
        if pattern is None:
            return text

        def replace(match: re.Match[str]) -> str:
            word = match.group(1)
            if word in sensitive:
                return sensitive[word]
            return insensitive.get(word.casefold(), word)

        out, last = [], 0
        for span in _PROTECTED.finditer(text):
            out.append(pattern.sub(replace, text[last : span.start()]))
            out.append(span.group(0))
            last = span.end()
        out.append(pattern.sub(replace, text[last:]))
        return "".join(out)


class PronunciationStore:
    """User-defined rules (SQLite table `pronunciation_rules`, schema v3)."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db

    async def list(self) -> list[Rule]:
        async with self.db.execute("SELECT * FROM pronunciation_rules ORDER BY term COLLATE NOCASE") as cur:
            rows = await cur.fetchall()
        return [
            Rule(r["term"], r["replacement"], r["engine"], bool(r["case_sensitive"]), bool(r["enabled"]), r["id"], False, True)
            for r in rows
        ]

    async def add(self, term: str, replacement: str, engine: str, case_sensitive: bool) -> Rule:
        rule = Rule(term.strip(), replacement.strip(), engine, case_sensitive, True, uuid.uuid4().hex[:12])
        now = time.time()
        await self.db.execute(
            """INSERT INTO pronunciation_rules (id, term, replacement, engine, case_sensitive, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (rule.id, rule.term, rule.replacement, engine, int(case_sensitive), now, now),
        )
        await self.db.commit()
        return rule

    async def update(self, rule_id: str, **fields: object) -> Rule | None:
        allowed = {k: v for k, v in fields.items() if k in {"term", "replacement", "engine", "case_sensitive", "enabled"} and v is not None}
        if allowed:
            sets = ", ".join(f"{k} = ?" for k in allowed)
            values = [int(v) if isinstance(v, bool) else str(v).strip() for v in allowed.values()]
            await self.db.execute(f"UPDATE pronunciation_rules SET {sets}, updated_at = ? WHERE id = ?", (*values, time.time(), rule_id))
            await self.db.commit()
        return next((r for r in await self.list() if r.id == rule_id), None)

    async def delete(self, rule_id: str) -> bool:
        cur = await self.db.execute("DELETE FROM pronunciation_rules WHERE id = ?", (rule_id,))
        await self.db.commit()
        return cur.rowcount > 0


class PronunciationService:
    def __init__(self, store: PronunciationStore, enabled: Callable[[], bool]) -> None:
        self.store = store
        self.enabled = enabled
        self.pronouncer = Pronouncer()
        self.custom: list[Rule] = []

    async def reload(self) -> None:
        self.custom = await self.store.list()
        self.pronouncer.set_rules([*BUILTIN_RULES, *self.custom])

    def normalize(self, text: str, engine: str) -> str:
        if not self.enabled():
            return Pronouncer.cleanup(text)
        return self.pronouncer.apply(text, engine)

    def terms(self) -> list[str]:
        """Technical terms for the speech-recognition vocabulary hint (abbreviations excluded)."""
        seen: dict[str, None] = {}
        for rule in [*BUILTIN_RULES, *self.custom]:
            if rule.enabled and not rule.term.endswith(".") and len(rule.term) > 2 and rule.term not in ("TL",):
                seen.setdefault(rule.term, None)
        return list(seen)
