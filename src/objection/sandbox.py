"""Local sandbox without Docker (M3, D-015).

What it does, in order of strength:
- runs in a throw-away directory (callers copy the project there), HOME/TMP point inside it;
- scrubbed environment: no API keys, tokens or secrets of the parent process; OBJECTION_NESTED=1 blocks recursion;
- its own process group, killed as a whole on timeout;
- POSIX rlimits: CPU seconds, address space, file size, no core dumps;
- no network on Linux via an unprivileged user+network namespace (`unshare -rn`) when the kernel allows it.
It is NOT a security boundary against hostile code (no filesystem jail; macOS/Windows get only timeout, env and temp
dir). `capabilities()` reports what is active so the UI and the docs can say it honestly.
"""

from __future__ import annotations

import functools
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import SandboxConfig

KEEP_ENV = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "VIRTUAL_ENV"}


@functools.lru_cache(maxsize=1)
def _unshare_ok() -> bool:
    if not sys.platform.startswith("linux") or not shutil.which("unshare"):
        return False
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def capabilities(cfg: SandboxConfig | None = None) -> dict[str, Any]:
    cfg = cfg or SandboxConfig()
    posix = os.name == "posix"
    return {
        "platform": sys.platform,
        "temp_dir": True,
        "env_scrubbed": True,
        "process_group_kill": True,
        "rlimits": posix,
        "network_isolated": (not cfg.network) and _unshare_ok(),
        "network_isolation_available": _unshare_ok(),
        "docker": False,
        "note": "not a security boundary: no filesystem jail; use trusted models or a VM for hostile code",
    }


def clean_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in KEEP_ENV}
    env.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(home), TMP=str(home), TEMP=str(home),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", OBJECTION_NESTED="1", NO_COLOR="1")
    env.update(extra or {})
    return env


def _limits(cfg: SandboxConfig):
    def apply() -> None:  # runs in the child between fork and exec
        import resource

        def lim(kind: int, value: int) -> None:
            try:
                soft, hard = resource.getrlimit(kind)
                v = value if hard == resource.RLIM_INFINITY else min(value, hard)
                resource.setrlimit(kind, (v, v))
            except (ValueError, OSError):
                pass

        lim(resource.RLIMIT_CPU, cfg.cpu_s)
        lim(resource.RLIMIT_AS, cfg.memory_mb * 1024 * 1024)
        lim(resource.RLIMIT_FSIZE, 256 * 1024 * 1024)
        lim(resource.RLIMIT_CORE, 0)
    return apply


def run(command: str, cwd: Path, cfg: SandboxConfig | None = None, *, timeout_s: float | None = None,
        env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run a shell command inside `cwd` with the sandbox measures above. Never raises for the command's failures."""
    cfg = cfg or SandboxConfig()
    timeout = timeout_s or cfg.timeout_s
    isolated = (not cfg.network) and _unshare_ok()
    posix = os.name == "posix"
    argv: list[str] | str
    if isolated:
        argv = ["unshare", "-rn", "sh", "-c", command]
    elif posix:
        argv = ["sh", "-c", command]
    else:
        argv = command
    kwargs: dict[str, Any] = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  env=clean_env(Path(cwd), env))
    if posix:
        kwargs.update(start_new_session=True, preexec_fn=_limits(cfg))
    else:
        kwargs.update(shell=True, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    started = time.perf_counter()
    timed_out = False
    try:
        p = subprocess.Popen(argv, **kwargs)
    except OSError as exc:
        return {"exit_code": -1, "output": f"cannot start: {exc}", "duration_s": 0.0, "timed_out": False,
                "network_isolated": isolated}
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill(p)
        out, _ = p.communicate()
    text = (out or b"").decode("utf-8", "replace")
    limit = cfg.max_output_kb * 1024
    if len(text) > limit:
        text = text[: limit // 4] + "\n…[output truncated]…\n" + text[-(limit * 3 // 4):]
    if timed_out:
        text += f"\n[timeout after {timeout:.0f}s: process group killed]"
    return {"exit_code": -1 if timed_out else p.returncode, "output": text.strip(),
            "duration_s": round(time.perf_counter() - started, 2), "timed_out": timed_out,
            "network_isolated": isolated}


def _kill(p: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(p.pid, signal.SIGKILL)
        else:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    except (OSError, ProcessLookupError):
        try:
            p.kill()
        except OSError:
            pass


def run_python(code: str, cfg: SandboxConfig | None = None, timeout_s: float = 20) -> dict[str, Any]:
    """Run a Python snippet in an empty temp dir (isolated mode `-I`)."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="objection-py-") as tmp:
        (Path(tmp) / "snippet.py").write_text(code, encoding="utf-8")
        exe = sys.executable.replace("\\", "/")
        return run(f'"{exe}" -I snippet.py', Path(tmp), cfg, timeout_s=timeout_s)
