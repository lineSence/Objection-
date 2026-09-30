"""Answer normalisation and weighted voting for `verify` / `quick` (M2)."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_TRUE = {"true", "yes", "да", "верно", "правда", "correct", "истина", "confirmed", "y"}
_FALSE = {"false", "no", "нет", "неверно", "ложь", "incorrect", "неправда", "refuted", "n"}
_UNKNOWN = {"unknown", "unsure", "неизвестно", "не знаю", "unclear", "n/a", "cannot determine"}
_NUM = re.compile(r"^[~≈]?\s*(-?\d[\d\s]*(?:[.,]\d+)?)\s*(%|[a-zа-я]{0,6})?$", re.I)


def normalize(answer: Any) -> str:
    s = unicodedata.normalize("NFKC", str(answer or "")).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .!;:\"'«»`()[]")
    s = re.sub(r"^(the|a|an)\s+", "", s)
    if s in _TRUE:
        return "true"
    if s in _FALSE:
        return "false"
    if s in _UNKNOWN or not s:
        return "unknown"
    m = _NUM.match(s)
    if m:
        n = float(m.group(1).replace(" ", "").replace(",", "."))
        unit = m.group(2) or ""
        return (str(int(n)) if n == int(n) else f"{n:g}") + unit
    return s


def tally(answers: list[dict[str, Any]], weights: dict[str, float]) -> dict[str, Any]:
    """answers: [{model_id, answer, confidence}] → votes sorted by weight, winner, agreement, tie.

    Weight of a vote = model weight × (0.5 + 0.5 × confidence): confident answers count more, but never double.
    """
    groups: dict[str, dict[str, Any]] = {}
    for a in answers:
        key = normalize(a.get("answer"))
        conf = a.get("confidence")
        w = weights.get(a["model_id"], 1.0) * (0.5 + 0.5 * (conf if isinstance(conf, (int, float)) else 0.5))
        g = groups.setdefault(key, {"key": key, "answer": str(a.get("answer") or "").strip(), "models": [], "weight": 0.0})
        g["models"].append(a["model_id"])
        g["weight"] = round(g["weight"] + w, 4)
    votes = sorted(groups.values(), key=lambda g: (-g["weight"], -len(g["models"])))
    total = sum(g["weight"] for g in votes) or 1.0
    winner = votes[0] if votes else None
    tie = len(votes) > 1 and abs(votes[0]["weight"] - votes[1]["weight"]) < 1e-9
    return {
        "votes": votes,
        "winner": winner,
        "tie": tie,
        "unanimous": len(votes) == 1 and len(answers) >= 2,
        "share": round(winner["weight"] / total, 3) if winner else 0.0,
        "agreement": f"{len(winner['models'])}/{len(answers)}" if winner else None,
    }
