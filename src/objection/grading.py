"""Grading an answer against a known correct one (labels, eval). Lenient on format, strict on the value."""

from __future__ import annotations

import re

from .voting import normalize

_NUM = re.compile(r"-?\d[\d\s,]*(?:\.\d+)?")


def _num(s: str) -> str | None:
    s = s.strip().replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):  # 1,234.5
        s = s.replace(",", "")
    k = normalize(s)
    return k if re.fullmatch(r"-?\d+(\.\d+)?", k) else None


def expected_key(expected: str) -> str:
    """GSM8K-style "reasoning #### 42" → "42"; everything else through `normalize`."""
    e = str(expected or "")
    if "####" in e:
        e = e.rsplit("####", 1)[1]
    return normalize(e)


def grade(answer: str | None, expected: str | None) -> bool | None:
    """True/False when it can be decided, None when there is nothing to compare."""
    if answer is None or expected is None or not str(expected).strip():
        return None
    ek = expected_key(expected)
    text = str(answer)
    if normalize(text) == ek:
        return True
    if re.fullmatch(r"-?\d+(\.\d+)?", ek):  # numeric: the last number of the answer, or any number of its first line
        nums = [n for n in (_num(m) for m in _NUM.findall(text)) if n is not None]
        first = [n for n in (_num(m) for m in _NUM.findall(text.strip().splitlines()[0] if text.strip() else "")) if n]
        return bool(nums) and (nums[-1] == ek or ek in first)
    if ek in ("true", "false"):
        head = re.split(r"[\s,.;:—-]+", text.strip(), maxsplit=1)[0] if text.strip() else ""
        return normalize(head) == ek
    if len(ek) <= 60:
        return re.search(r"(?<!\w)" + re.escape(ek) + r"(?!\w)", normalize(text)) is not None
    return False
