"""Content fingerprint of a project directory for the result cache (M3.1).

The cache must not return a result computed for other files: `code` / `review` / repo evidence depend on what is in
`workdir`, not on its path. The fingerprint hashes relative paths and file contents (large files: size + mtime),
skipping the same directories the sandbox copy skips. Bounded: after `max_files` files the count and a marker are
hashed, so huge trees stay fast while any change in the first files still changes the key.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
             "dist", "build", ".tox", ".idea", ".vscode"}
MAX_HASHED_BYTES = 1_000_000


def walk(root: str | Path, max_files: int = 5000):
    """Yield (relative posix path, absolute path) in a stable order, skipping build/VCS/venv folders."""
    root = Path(root)
    n = 0
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        for name in sorted(names):
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            yield p.relative_to(root).as_posix(), p
            n += 1
            if n >= max_files:
                return


def directory(root: str | Path | None, max_files: int = 5000) -> str | None:
    if not root or not os.path.isdir(root):
        return None
    h = hashlib.sha256()
    count = 0
    for rel, p in walk(root, max_files):
        count += 1
        h.update(rel.encode() + b"\0")
        try:
            st = p.stat()
            if st.st_size <= MAX_HASHED_BYTES:
                h.update(hashlib.sha256(p.read_bytes()).digest())
            else:
                h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
        except OSError:
            h.update(b"unreadable")
    h.update(f"files={count}".encode())
    return h.hexdigest()
