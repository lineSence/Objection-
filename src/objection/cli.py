"""Command-line interface: `objection ask`, `objection models`, `objection runs`, `objection ui`."""

from __future__ import annotations

import asyncio
import json
import sys
import webbrowser

import typer

from .config import load_config
from .engine import Engine
from .providers import health_check
from .schemas import RunRequest
from .store import RunStore

app = typer.Typer(help="Objection! — a council of LLMs.", no_args_is_help=True)
models_app = typer.Typer(help="The model pool (defined by you in the config).")
runs_app = typer.Typer(help="Run history.")
app.add_typer(models_app, name="models")
app.add_typer(runs_app, name="runs")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question for the council; '-' reads from stdin."),
    context: str | None = typer.Option(None, "--context", "-c", help="Extra context."),
    models: str | None = typer.Option(None, "--models", "-m", help="Comma-separated model ids."),
    budget: float | None = typer.Option(None, "--budget", help="Budget in USD."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    source: str = typer.Option("cli", "--source", help="Run source label (cli, opencode, cline…)."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore cached identical runs."),
) -> None:
    """Ask the council (preset `deliberate`)."""
    if question == "-":
        question = sys.stdin.read()
    config = load_config()
    engine = Engine(config, RunStore(config.storage.resolved))
    req = RunRequest(
        question=question, context=context, source=source, budget_usd=budget, no_cache=no_cache,
        models=[m.strip() for m in models.split(",")] if models else None,
    )
    run = asyncio.run(engine.ask(req))
    if as_json:
        typer.echo(run.model_dump_json(indent=2))
    elif run.status != "done" or not run.verdict:
        typer.secho(f"Run {run.id} failed: {run.error}", fg="red", err=True)
    else:
        v = run.verdict
        typer.secho("Objection! — итог совета", bold=True)
        typer.echo(v.answer)
        if v.agreement or v.confidence is not None:
            typer.echo(f"\nСогласие: {v.agreement or '—'} · уверенность: {v.confidence if v.confidence is not None else '—'}")
        for d in v.disputed:
            typer.echo(f"Спорно: {d.get('point')}")
        if v.minority_report:
            typer.echo(f"Особое мнение: {v.minority_report}")
        typer.echo(f"\nrun {run.id} · ${run.cost_usd:.4f} · {run.latency_s} с{' · из кэша' if run.cached else ''}")
    raise typer.Exit(0 if run.status == "done" else 1)


SEVERITIES = ["critical", "high", "medium", "low", "info"]
EXIT = {"pass": 0, "fail": 1, "uncertain": 2}


@app.command()
def review(
    path: str | None = typer.Argument(None, help="File to review; '-' reads stdin. Omit to use --diff/--staged."),
    diff: str | None = typer.Option(None, "--diff", help="Review `git diff <REF>` (e.g. HEAD~1, main...)."),
    staged: bool = typer.Option(False, "--staged", help="Review `git diff --staged`."),
    kind: str = typer.Option(None, "--kind", help="diff | plan | file | text (default: inferred)."),
    instructions: str = typer.Option("Find real problems: bugs, security, broken logic, missing edge cases.",
                                     "--instructions", "-i"),
    fail_on: str = typer.Option("high", "--fail-on", help="Minimal confirmed severity that fails: " + "|".join(SEVERITIES)),
    models: str | None = typer.Option(None, "--models", "-m"),
    budget: float | None = typer.Option(None, "--budget"),
    fmt: str = typer.Option("text", "--format", "-f", help="text | json"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Council review. Exit code: 0 pass, 1 fail, 2 uncertain, 3 error."""
    import subprocess

    if fail_on not in SEVERITIES:
        typer.secho(f"--fail-on must be one of {SEVERITIES}", fg="red", err=True)
        raise typer.Exit(3)
    if diff or staged or path is None:
        cmd = ["git", "diff", "--staged"] if staged else ["git", "diff", diff] if diff else ["git", "diff", "HEAD"]
        try:
            target = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            typer.secho(f"git diff failed: {exc}", fg="red", err=True)
            raise typer.Exit(3)
        kind = kind or "diff"
    elif path == "-":
        target, kind = sys.stdin.read(), kind or "text"
    else:
        from pathlib import Path

        target = Path(path).read_text(encoding="utf-8")
        kind = kind or ("diff" if path.endswith((".diff", ".patch")) else "file")
        target = f"# {path}\n{target}" if kind == "file" else target
    if not target.strip():
        typer.secho("nothing to review (empty diff)", fg="yellow", err=True)
        raise typer.Exit(0)
    config = load_config()
    engine = Engine(config, RunStore(config.storage.resolved))
    try:
        run = asyncio.run(engine.ask(RunRequest(
            question=instructions, mode="review", target=target, target_kind=kind, fail_on=fail_on, source="cli",
            budget_usd=budget, no_cache=no_cache, models=[m.strip() for m in models.split(",")] if models else None)))
    except (KeyError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(3)
    if fmt == "json":
        typer.echo(run.model_dump_json(indent=2))
    elif run.status != "done" or not run.verdict:
        typer.secho(f"Run {run.id} failed: {run.error}", fg="red", err=True)
    else:
        v = run.verdict
        color = {"pass": "green", "fail": "red", "uncertain": "yellow"}[v.verdict or "uncertain"]
        typer.secho(f"Objection! review: {v.verdict}", fg=color, bold=True)
        for f in v.findings:
            if f.status == "rejected":
                continue
            loc = f" ({f.location})" if f.location else ""
            typer.echo(f"  [{f.severity}] [{f.status} {len(f.confirmed_by)}+/{len(f.refuted_by)}-] {f.title}{loc}")
            if f.suggestion:
                typer.echo(f"      → {f.suggestion}")
        rejected = sum(f.status == "rejected" for f in v.findings)
        if rejected:
            typer.echo(f"  … {rejected} finding(s) rejected by cross-check")
        typer.echo(f"\nrun {run.id} · ${run.cost_usd:.4f} · {run.latency_s} с{' · cached' if run.cached else ''}")
    if run.status != "done" or not run.verdict:
        raise typer.Exit(3)
    raise typer.Exit(EXIT[run.verdict.verdict or "uncertain"])


@app.command()
def mcp() -> None:
    """Run the MCP server over stdio (for OpenCode, Cline and other MCP clients)."""
    from .mcp_server import run_stdio

    run_stdio()


@models_app.command("list")
def models_list() -> None:
    """Show the model pool."""
    for m in load_config().models:
        flags = " ".join(f for f, on in (("local", m.is_local), ("disabled", not m.enabled)) if on)
        typer.echo(f"{m.id:<16} {m.model:<40} {flags}")


@models_app.command("check")
def models_check() -> None:
    """Health-check every enabled model in the pool."""
    config = load_config()

    async def go():
        return await asyncio.gather(*(health_check(m) for m in config.enabled_models))

    bad = 0
    for m, (ok, detail, lat) in zip(config.enabled_models, asyncio.run(go())):
        bad += not ok
        typer.secho(f"{m.id:<16} {'ok' if ok else 'FAIL':<5} {lat:5.1f} s  {'' if ok else detail}",
                    fg="green" if ok else "red")
    raise typer.Exit(1 if bad else 0)


@runs_app.command("list")
def runs_list(limit: int = 20) -> None:
    config = load_config()
    for r in RunStore(config.storage.resolved).list_runs(limit):
        typer.echo(f"{r.id}  {r.status:<8} {r.source:<9} ${r.cost_usd:.4f}  {r.question[:60]}")


@runs_app.command("show")
def runs_show(run_id: str) -> None:
    config = load_config()
    run = RunStore(config.storage.resolved).get_run(run_id)
    if not run:
        raise typer.Exit(1)
    typer.echo(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2))


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Bind address. Keep 127.0.0.1 unless you know why."),
    port: int = typer.Option(8765),
    open_browser: bool = typer.Option(True, "--open/--no-open"),
) -> None:
    """Start the local Web UI."""
    import uvicorn

    from .server import create_app

    url = f"http://{host}:{port}"
    typer.echo(f"Objection! Web UI: {url}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
