"""When to use the model's thinking mode.

Measured on 2026-10-16 with qwen3.5:9b, six multi-step arithmetic/logic/code questions, two runs:
fast mode 2/6 correct (0.1–0.6 s), thinking mode 6/6 correct (4.6–8.7 s). Thinking is therefore worth
its latency only for genuinely multi-step problems, which this deliberately strict heuristic detects.
"""

from __future__ import annotations

import re

_NUMBER = re.compile(
    r"\d+(?:[.,]\d+)?|\b(yarısı|çeyreği|(iki|üç|dört|beş|altı|yedi|sekiz|dokuz|on)(de|te|da|ta) (bir|iki|üç)\w*)",
    re.IGNORECASE,
)  # digits, plus Turkish fractions ("üçte biri") so word problems are recognised
_OPERATOR = re.compile(r"[×x*/+÷=^-]|\b(artı|eksi|çarpı|bölü|katı|kare|karekök|yüzde)\b", re.IGNORECASE)
_QUANTITY_QUESTION = re.compile(r"\b(kaç|kaçtır|ne kadar|hesapla|how many|how much|calculate)\b", re.IGNORECASE)
_EXPLICIT = re.compile(
    r"\b(adım adım|derinlemesine|detaylıca düşün|iyice düşün|dikkatlice düşün|mantık yürüt|kanıtla|ispatla|"
    r"step by step|think (carefully|hard)|reason through|prove)\b",
    re.IGNORECASE,
)
_DEBUG = re.compile(r"\b(hata|bug|neden çalışmıyor|çalışmıyor|exception|traceback|error|debug)\w*", re.IGNORECASE)


def needs_deep_reasoning(text: str) -> bool:
    if _EXPLICIT.search(text):
        return True
    numbers = _NUMBER.findall(text)
    if len(numbers) >= 3 and (_OPERATOR.search(text) or _QUANTITY_QUESTION.search(text)):
        return True  # multi-step arithmetic
    if len(numbers) >= 2 and _QUANTITY_QUESTION.search(text) and len(text) >= 60:
        return True  # a word problem ("35 baş ve 94 ayak… kaç tavşan?"), not "2 artı 2 kaç eder?"
    if "```" in text and _DEBUG.search(text):
        return True  # pasted code that needs debugging
    return False
