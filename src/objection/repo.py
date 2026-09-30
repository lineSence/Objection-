"""Repository files as evidence for the Verifier (M3.1).

A plain-Python grep over the run's `workdir`: no index, no external tools, read-only. Paths never leave the root
(symlinks are skipped), binary and large files are ignored, and every snippet reaches a model only inside an
<untrusted> block (the same rule as for web pages).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .fingerprint import walk

MAX_FILE_BYTES = 512_000
CONTEXT_LINES = 3
_STOP = {"the", "and", "for", "not", "with", "that", "this", "from", "into", "when", "then", "than", "does", "should",
         "must", "will", "are", "was", "has", "have", "can", "cannot", "file", "function", "method", "class", "code",
         "value", "line", "lines", "это", "что", "как", "для", "при", "или", "если", "функция", "файл", "метод",
         "класс", "значение", "строка", "код", "нет", "есть", "не"}
_TOKEN = re.compile(r"[A-Za-z_][\w.]*\w|[A-Za-z_]\w*|\d{2,}|[а-яА-ЯёЁ]{4,}")
_QUOTED = re.compile(r"[\"'`«]([^\"'`»]{3,120})[\"'`»]")


def terms(query: str) -> tuple[list[str], list[str]]:
    """(exact phrases, words). Phrases: quoted parts of the query; words: identifiers and long words."""
    phrases = [p.strip() for p in _QUOTED.findall(query) if p.strip()]
    rest = _QUOTED.sub(" ", query)
    words = []
    for t in _TOKEN.findall(rest):
        t = t.strip(".")
        if len(t) >= 3 and t.lower() not in _STOP and t not in words:
            words.append(t)
    return phrases, words[:12]


def _text(p: Path) -> str | None:
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = p.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:4096]:
        return None
    return raw.decode("utf-8", errors="replace")


def search(root: str | Path | None, query: str, k: int = 6, path_hint: str | None = None,
           max_files: int = 5000) -> list[dict[str, Any]]:
    """Best-matching line windows for `query` in `root`. Returns [{title, url, snippet, path, line, score}]."""
    if not root or not Path(root).is_dir():
        return []
    phrases, words = terms(query)
    if not phrases and not words:
        return []
    lowered = [w.lower() for w in words]
    hint = (path_hint or "").strip().lstrip("./").split(":", 1)[0].lower()
    hits: list[tuple[float, str, int, list[str]]] = []
    for rel, p in walk(root, max_files):
        body = _text(p)
        if body is None:
            continue
        low_body = body.lower()
        rel_low = rel.lower()
        path_bonus = 2.0 if hint and (hint in rel_low or rel_low.endswith(hint)) else 0.0
        name_bonus = sum(0.5 for w in lowered if w in rel_low)
        if not path_bonus and not any(ph.lower() in low_body for ph in phrases) \
                and not any(w in low_body for w in lowered):
            continue
        lines = body.splitlines()
        for i, line in enumerate(lines):
            low = line.lower()
            score = sum(3.0 for ph in phrases if ph.lower() in low)
            score += sum(1.0 + (0.5 if ("_" in w or any(ch.isupper() for ch in w[1:])) else 0.0)
                         for w, lw in zip(words, lowered) if lw in low)
            if score > 0:
                hits.append((score + path_bonus + name_bonus, rel, i, lines))
    hits.sort(key=lambda h: (-h[0], h[1], h[2]))
    out: list[dict[str, Any]] = []
    taken: dict[str, list[int]] = {}
    for score, rel, i, lines in hits:
        if any(abs(i - j) <= CONTEXT_LINES * 2 for j in taken.get(rel, [])):
            continue  # one window per region
        taken.setdefault(rel, []).append(i)
        lo, hi = max(0, i - CONTEXT_LINES), min(len(lines), i + CONTEXT_LINES + 1)
        snippet = "\n".join(f"{n + 1:>5} | {lines[n][:240]}" for n in range(lo, hi))
        out.append({"title": f"{rel}:{i + 1}", "url": f"repo://{rel}#L{i + 1}", "snippet": snippet, "path": rel,
                    "line": i + 1, "score": round(score, 2), "engine": "repo"})
        if len(out) >= k:
            break
    return out
