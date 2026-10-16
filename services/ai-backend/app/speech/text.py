"""Text utilities for speech: incremental sentence splitting and markdown → speakable text."""

from __future__ import annotations

import re

_CODE_BLOCK = re.compile(r"```.*?(```|$)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_URL = re.compile(r"https?://\S+")
_CITATION = re.compile(r"\s?\[(\d+)\](?:\[(\d+)\])*")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:-]+\|[\s|:-]*$", re.MULTILINE)
_EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff\U0001f900-\U0001f9ff‍️]+")
_SPACES = re.compile(r"[ \t]+")
_BRAND = re.compile(r"\bLAZOS\b")


def to_speakable(text: str) -> str:
    text = _CODE_BLOCK.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _CITATION.sub("", text)
    text = _TABLE_RULE.sub("", text)
    text = text.replace("|", ", ")
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _EMOJI.sub("", text)
    text = text.replace("%", " yüzde ").replace("&", " ve ").replace("→", ", ").replace("—", ", ")
    text = _BRAND.sub("Lazos", text)  # all-caps names get spelled out by the phonemizer
    text = _SPACES.sub(" ", text)
    return text.strip()


# Sentence boundary: terminal punctuation followed by whitespace, or a newline (paragraph/list item).
_BOUNDARY = re.compile(r"(?<=[.!?…:;])\s+|\n+")
_ABBREVIATIONS = {"dr", "prof", "vb", "vs", "örn", "bkz", "mr", "mrs", "st", "no", "yy", "sn", "e.g", "i.e", "ör"}


class SentenceSplitter:
    """Accumulates streamed text and yields speakable sentences as soon as they are complete.

    The first chunk is released early (shorter minimum) so speech can start quickly; later chunks are
    merged into reasonably sized phrases for natural prosody.
    """

    def __init__(self, first_min_chars: int = 18, min_chars: int = 50, max_chars: int = 280) -> None:
        self._buf = ""
        self._in_code = False
        self._first = True
        self._first_min = first_min_chars
        self._min = min_chars
        self._max = max_chars

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while True:
            chunk = self._take()
            if chunk is None:
                break
            if speakable := to_speakable(chunk):
                out.append(speakable)
                self._first = False
        return out

    def flush(self) -> list[str]:
        rest, self._buf = self._buf, ""
        speakable = to_speakable(rest)
        return [speakable] if speakable else []

    def _take(self) -> str | None:
        # Skip fenced code blocks entirely: wait for the closing fence before continuing.
        fence = self._buf.find("```")
        if fence != -1:
            end = self._buf.find("```", fence + 3)
            if end == -1:
                # Speak what precedes the code block, hold the rest.
                if fence > 0 and to_speakable(self._buf[:fence]):
                    head, self._buf = self._buf[:fence], self._buf[fence:]
                    return head
                return None
            self._buf = self._buf[:fence] + " " + self._buf[end + 3 :]

        minimum = self._first_min if self._first else self._min
        for match in _BOUNDARY.finditer(self._buf):
            cut = match.start()
            head = self._buf[:cut]
            if len(head.strip()) < minimum and "\n" not in match.group(0):
                continue
            if self._is_abbreviation(head) or self._is_decimal(cut):
                continue
            self._buf = self._buf[match.end() :]
            return head
        if len(self._buf) > self._max:
            # Very long clause without punctuation: break at the last comma/space.
            cut = max(self._buf.rfind(", ", 0, self._max), self._buf.rfind(" ", 0, self._max))
            if cut > self._min:
                head, self._buf = self._buf[: cut + 1], self._buf[cut + 1 :]
                return head
        return None

    @staticmethod
    def _is_abbreviation(head: str) -> bool:
        last = head.rstrip(".").rsplit(" ", 1)[-1].lower()
        return head.endswith(".") and last in _ABBREVIATIONS

    def _is_decimal(self, cut: int) -> bool:
        return cut > 0 and self._buf[cut - 1] == "." and cut + 1 < len(self._buf) and self._buf[cut + 1].isdigit()
