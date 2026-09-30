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
eval_app = typer.Typer(help="Eval Harness: the council vs baselines at equal budget, on your pool.")
app.add_typer(models_app, name="models")
app.add_typer(runs_app, name="runs")
app.add_typer(eval_app, name="eval")


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question for the council; '-' reads from stdin."),
    context: str | None = typer.Option(None, "--context", "-c", help="Extra context."),
    models: str | None = typer.Option(None, "--models", "-m", help="Comma-separated model ids."),
    budget: float | None = typer.Option(None, "--budget", help="Budget in USD."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    source: str = typer.Option("cli", "--source", help="Run source label (cli, opencode, cline…)."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore cached identical runs."),
    mode: str | None = typer.Option(None, "--mode", help="auto | deliberate | verify | quick (default: config, auto)."),
    check: bool | None = typer.Option(None, "--check/--no-check", help="Fact-check claims (default: config)."),
    workdir: str | None = typer.Option(None, "--workdir", "-w", help="Project whose files may serve as evidence."),
) -> None:
    """Ask the council. `auto` routes to the cheapest fitting protocol."""
    if question == "-":
        question = sys.stdin.read()
    config = load_config()
    engine = Engine(config, RunStore(config.storage.resolved))
    if mode and mode not in ("auto", "deliberate", "verify", "quick"):
        typer.secho("--mode must be auto, deliberate, verify or quick", fg="red", err=True)
        raise typer.Exit(3)
    req = RunRequest(
        question=question, context=context, source=source, budget_usd=budget, no_cache=no_cache, mode=mode,
        check_facts=check, workdir=_abs(workdir),
        models=[m.strip() for m in models.split(",")] if models else None,
    )
    try:
        run = asyncio.run(engine.ask(req))
    except (KeyError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(3)
    if as_json:
        typer.echo(run.model_dump_json(indent=2))
    elif run.status != "done" or not run.verdict:
        typer.secho(f"Run {run.id} failed: {run.error}", fg="red", err=True)
    else:
        v = run.verdict
        typer.secho(f"Objection! — итог совета ({_mode_line(run)})", bold=True)
        typer.echo(v.answer)
        _print_votes(v)
        if v.agreement or v.confidence is not None:
            typer.echo(f"\nСогласие: {v.agreement or '—'} · уверенность: {v.confidence if v.confidence is not None else '—'}")
        for d in v.disputed:
            typer.echo(f"Спорно: {d.get('point')}")
        if v.minority_report:
            typer.echo(f"Особое мнение: {v.minority_report}")
        typer.echo(f"\nrun {run.id} · ${run.cost_usd:.4f} · {run.latency_s} с{' · из кэша' if run.cached else ''}")
    raise typer.Exit(0 if run.status == "done" else 1)


def _abs(path: str | None) -> str | None:
    from pathlib import Path

    return str(Path(path).resolve()) if path else None


def _mode_line(run) -> str:
    s = run.mode + (f" ← auto: {run.route_reason}" if run.requested_mode == "auto" and run.route_reason else "")
    v = run.verdict
    if v and v.stopped_early:
        s += f" · ранняя остановка, сэкономлено ≈${v.saved_usd_est:.4f}"
    if v and v.escalated_to:
        s += f" · эскалация → {v.escalated_to}"
    if run.budget_exhausted:
        s += " · бюджет исчерпан"
    return s


def _print_votes(v) -> None:
    for g in v.votes:
        typer.echo(f"  {g['weight']:>5.2f}  {g['answer'][:60]:<60}  {', '.join(g['models'])}")
    _print_claims(v)


CLAIM_MARK = {"supported": ("✓", "green"), "refuted": ("✗", "red"), "unverified": ("?", "yellow")}


def _print_claims(v) -> None:
    if v.fact_override:
        typer.secho(f"Фактчек перевесил голосование: {v.fact_override}", fg="yellow")
    if v.revised:
        typer.secho("Ответ исправлен после фактчека.", fg="yellow")
    if v.claims:
        typer.echo("\nПроверка фактов:")
    for c in v.claims:
        mark, color = CLAIM_MARK[c.status]
        typer.secho(f"  {mark} [{c.method}] {c.text[:100]}", fg=color)
        if c.evidence:
            typer.echo(f"      {c.evidence[:160]}")
        for src in c.sources[:2]:
            typer.echo(f"      {src['url']}")


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
    check: bool | None = typer.Option(None, "--check/--no-check",
                                      help="Fact-check findings against the repository / Python / web (default: config)."),
    workdir: str | None = typer.Option(None, "--workdir", "-w",
                                       help="Repository used as evidence (default: current directory for git/files)."),
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
            budget_usd=budget, no_cache=no_cache, check_facts=check,
            workdir=_abs(workdir) or (None if path == "-" else _abs(".")),
            models=[m.strip() for m in models.split(",")] if models else None)))
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
            ev = {"supported": " ✓fact", "refuted": " ✗fact", "unverified": " ?fact"}.get(f.evidence or "", "")
            typer.echo(f"  [{f.severity}] [{f.status} {len(f.confirmed_by)}+/{len(f.refuted_by)}-{ev}] {f.title}{loc}")
            if f.suggestion:
                typer.echo(f"      → {f.suggestion}")
        rejected = sum(f.status == "rejected" for f in v.findings)
        if rejected:
            typer.echo(f"  … {rejected} finding(s) rejected by cross-check or evidence")
        _print_claims(v)
        typer.echo(f"\nrun {run.id} · ${run.cost_usd:.4f} · {run.latency_s} с{' · cached' if run.cached else ''}")
    if run.status != "done" or not run.verdict:
        raise typer.Exit(3)
    raise typer.Exit(EXIT[run.verdict.verdict or "uncertain"])


@app.command()
def verify(
    claim: str = typer.Argument(..., help="Claim to fact-check; '-' reads stdin."),
    context: str | None = typer.Option(None, "--context", "-c"),
    models: str | None = typer.Option(None, "--models", "-m"),
    budget: float | None = typer.Option(None, "--budget"),
    as_json: bool = typer.Option(False, "--json"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    check: bool | None = typer.Option(None, "--check/--no-check", help="Check candidates with search / Python."),
) -> None:
    """Fact-check a claim (preset `verify`). Exit code: 0 confirmed, 1 refuted, 2 unverified, 3 error."""
    from .voting import normalize

    if claim == "-":
        claim = sys.stdin.read().strip()
    config = load_config()
    engine = Engine(config, RunStore(config.storage.resolved))
    q = f"Claim: {claim}\nIs this claim true? Answer exactly true, false or unknown."
    try:
        run = asyncio.run(engine.ask(RunRequest(question=q, context=context, mode="verify", source="cli",
                                                budget_usd=budget, no_cache=no_cache, check_facts=check,
                                                models=[m.strip() for m in models.split(",")] if models else None)))
    except (KeyError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(3)
    if run.status != "done" or not run.verdict:
        typer.secho(f"Run {run.id} failed: {run.error}", fg="red", err=True)
        raise typer.Exit(3)
    v = run.verdict
    key = normalize(v.votes[0]["answer"]) if v.votes else "unknown"
    status = {"true": "confirmed", "false": "refuted"}.get(key, "unverified") if v.verdict != "uncertain" else "unverified"
    if as_json:
        typer.echo(json.dumps({"status": status, **run.model_dump(mode="json")}, ensure_ascii=False, indent=2))
    else:
        color = {"confirmed": "green", "refuted": "red", "unverified": "yellow"}[status]
        typer.secho(f"Objection! verify: {status}  ({v.agreement}, {_mode_line(run)})", fg=color, bold=True)
        _print_votes(v)
        if v.votes and v.votes[0].get("reasoning"):
            typer.echo(f"\n{v.votes[0]['reasoning']}")
        if v.minority_report:
            typer.echo(f"Особое мнение: {v.minority_report}")
        typer.echo(f"\nrun {run.id} · ${run.cost_usd:.4f} · {run.latency_s} с{' · cached' if run.cached else ''}")
    raise typer.Exit({"confirmed": 0, "refuted": 1, "unverified": 2}[status])


@app.command()
def solve(
    task: str = typer.Argument(..., help="Coding task; '-' reads stdin."),
    tests: str | None = typer.Option(None, "--tests", "-t", help="Command that must pass, e.g. 'pytest -q'."),
    workdir: str = typer.Option(".", "--workdir", "-w", help="Project directory (copied to a temp dir per candidate)."),
    file: str | None = typer.Option(None, "--file", "-f", help="Solution file path relative to workdir."),
    apply: bool = typer.Option(False, "--apply", help="Write the winning solution into workdir."),
    context: str | None = typer.Option(None, "--context", "-c"),
    models: str | None = typer.Option(None, "--models", "-m"),
    budget: float | None = typer.Option(None, "--budget"),
    as_json: bool = typer.Option(False, "--json"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Council writes code, your tests decide (preset `code`). Exit: 0 pass, 1 fail, 2 uncertain, 3 error.

    Model-written code is executed locally in a temporary copy of the workdir (sandbox: M3).
    """
    from pathlib import Path

    if task == "-":
        task = sys.stdin.read()
    config = load_config()
    engine = Engine(config, RunStore(config.storage.resolved))
    wd = str(Path(workdir).resolve())
    try:
        run = asyncio.run(engine.ask(RunRequest(question=task, context=context, mode="code", tests_cmd=tests,
                                                workdir=wd, solution_path=file, source="cli", budget_usd=budget,
                                                no_cache=no_cache,
                                                models=[m.strip() for m in models.split(",")] if models else None)))
    except (KeyError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(3)
    if run.status != "done" or not run.verdict or not run.verdict.solution:
        typer.secho(f"Run {run.id} failed: {run.error}", fg="red", err=True)
        raise typer.Exit(3)
    v = run.verdict
    sol = v.solution
    if apply and (sol.get("passed") or not tests):
        dest = Path(wd) / sol["filename"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(sol["code"], encoding="utf-8")
    if as_json:
        typer.echo(run.model_dump_json(indent=2))
    else:
        color = {"pass": "green", "fail": "red"}.get(v.verdict or "", "yellow")
        typer.secho(f"Objection! solve: {v.verdict or 'picked by chair'}", fg=color, bold=True)
        for c in v.candidates:
            mark = {True: "PASS", False: "FAIL", None: " —  "}[c.get("passed")]
            typer.echo(f"  [{mark}] round {c.get('round')} {c['model_id']:<16} {c['filename']}")
        typer.echo(f"\n{v.answer}")
        if apply:
            typer.echo(f"→ written to {sol['filename']}" if (sol.get("passed") or not tests) else "→ not applied: tests fail")
        else:
            typer.echo(f"\n----- {sol['filename']} -----\n{sol['code']}")
        typer.echo(f"run {run.id} · ${run.cost_usd:.4f} · {run.latency_s} с{' · cached' if run.cached else ''}")
    raise typer.Exit(EXIT.get(v.verdict or "uncertain", 2) if tests else 0)


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
        typer.echo(f"{m.id:<16} {m.model:<40} {m.family_name:<10} {flags}")


@models_app.command("add")
def models_add(
    model_id: str = typer.Argument(..., help="Short id used in the council, e.g. gpt."),
    model: str = typer.Argument(..., help="LiteLLM model string, e.g. openai/gpt-4o-mini or ollama/qwen2.5."),
    api_base: str | None = typer.Option(None, "--api-base"),
    api_key_env: str | None = typer.Option(None, "--api-key-env", help="Env var holding this model's key."),
    family: str | None = typer.Option(None, "--family", help="Model family (default: inferred from the name)."),
    weight: float = typer.Option(1.0, "--weight"),
    fallback: list[str] = typer.Option([], "--fallback", help="LiteLLM model string to try when this one fails."),
    disabled: bool = typer.Option(False, "--disabled"),
    replace: bool = typer.Option(False, "--replace", help="Overwrite an existing model with this id."),
) -> None:
    """Add a model to the pool (written to the config file)."""
    from .config import ModelSpec, config_path, save_config

    config = load_config()
    try:
        spec = ModelSpec(id=model_id, model=model, api_base=api_base, api_key_env=api_key_env, family=family,
                         weight=weight, fallbacks=list(fallback), enabled=not disabled)
    except ValueError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(3)
    exists = [m for m in config.models if m.id == model_id]
    if exists and not replace:
        typer.secho(f"model '{model_id}' already exists (use --replace)", fg="red", err=True)
        raise typer.Exit(1)
    if not config_path().exists():  # the implicit mock pool is not something the user chose
        config.models = [m for m in config.models if not m.model.startswith("mock/")]
    config.models = [m for m in config.models if m.id != model_id] + [spec]
    path = save_config(config)
    typer.echo(f"added {model_id} → {model} (family {spec.family_name}) in {path}")


@models_app.command("remove")
def models_remove(model_ids: list[str] = typer.Argument(...)) -> None:
    """Remove models from the pool. Their history (runs, statistics) is kept."""
    from .config import save_config

    config = load_config()
    missing = [i for i in model_ids if not any(m.id == i for m in config.models)]
    for i in missing:
        typer.secho(f"not in the pool: {i}", fg="red", err=True)
    config.models = [m for m in config.models if m.id not in model_ids]
    config.defaults.council.pinned = [i for i in config.defaults.council.pinned if i not in model_ids]
    if config.defaults.judge in model_ids:
        config.defaults.judge = "auto"
    path = save_config(config)
    typer.echo(f"pool: {', '.join(m.id for m in config.models) or '(empty)'} → {path}")
    raise typer.Exit(1 if missing else 0)


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
        mark = {"correct": "✓", "wrong": "✗"}.get(r.label or "", " ")
        typer.echo(f"{r.id} {mark} {r.status:<8} {r.mode:<10} {r.source:<9} ${r.cost_usd:.4f}  {r.question[:60]}")


@runs_app.command("delete")
def runs_delete(run_ids: list[str] = typer.Argument(..., help="Run ids to delete.")) -> None:
    config = load_config()
    store = RunStore(config.storage.resolved)
    missing = [r for r in run_ids if not store.delete_run(r)]
    for r in missing:
        typer.secho(f"not found: {r}", fg="red", err=True)
    raise typer.Exit(1 if missing else 0)


@runs_app.command("label")
def runs_label(
    run_id: str = typer.Argument(...),
    label: str = typer.Argument(..., help="correct | wrong | clear"),
    expected: str | None = typer.Option(None, "--expected", "-e", help="The correct answer (grades every model)."),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Label the final answer of a run. Labelled runs form your own eval set (`objection eval run --suite mine`)."""
    from .labels import set_label

    config = load_config()
    store = RunStore(config.storage.resolved)
    try:
        run = set_label(store, run_id, None if label == "clear" else label, expected=expected, note=note, config=config)
    except (KeyError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(f"{run.id}: {run.label or 'unlabelled'}" + (f" (expected: {run.expected})" if run.expected else ""))


@runs_app.command("reindex")
def runs_reindex() -> None:
    """Rebuild per-model outcome statistics from the stored runs and events."""
    from .outcomes import reindex

    config = load_config()
    n = reindex(RunStore(config.storage.resolved), config)
    typer.echo(f"reindexed {n} finished run(s)")


@app.command("sandbox")
def sandbox_check() -> None:
    """Show which sandbox measures are active on this machine."""
    from . import sandbox

    config = load_config()
    caps = sandbox.capabilities(config.sandbox)
    for k, v in caps.items():
        typer.echo(f"{k:<28} {v}")
    r = sandbox.run_python("import json, os\nprint(json.dumps({'keys_visible': [k for k in os.environ if 'KEY' in k]}))",
                           config.sandbox)
    typer.echo(f"{'probe':<28} exit {r['exit_code']}, {r['output'][:120]}")


@runs_app.command("show")
def runs_show(run_id: str) -> None:
    config = load_config()
    run = RunStore(config.storage.resolved).get_run(run_id)
    if not run:
        raise typer.Exit(1)
    typer.echo(json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2))


@eval_app.command("run")
def eval_run(
    suite: str = typer.Option("math-mini", "--suite", "-s", help="math-mini | code-mini | mine | path to .jsonl"),
    n: int | None = typer.Option(None, "--n", help="Use only the first N tasks."),
    presets: str | None = typer.Option(None, "--presets", "-p",
                                       help="Comma-separated presets (default: verify,quick; code for coding suites)."),
    models: str | None = typer.Option(None, "--models", "-m", help="Council to evaluate (default: config)."),
    check: bool = typer.Option(False, "--check/--no-check", help="Fact-check inside the presets (costs more)."),
    budget: float | None = typer.Option(None, "--budget", help="Budget per run in USD."),
    parallel: int = typer.Option(2, "--parallel", help="Concurrent runs."),
    sc_temperature: float = typer.Option(0.7, "--sc-temperature", help="Sampling temperature for self-consistency."),
    fmt: str = typer.Option("text", "--format", "-f", help="text | md | json"),
    out: str | None = typer.Option(None, "--out", "-o", help="Also write the report to this file."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask before spending money."),
) -> None:
    """Run an eval. Exit code: 0 every preset beats its baselines, 1 some preset does not, 3 error."""
    from .eval import ANSWER_PRESETS, Harness, load_suite, to_markdown

    config = load_config()
    store = RunStore(config.storage.resolved)
    try:
        tasks = load_suite(suite, store, n)
        ps = [p.strip() for p in presets.split(",")] if presets else None
        bad = [p for p in ps or [] if p not in (*ANSWER_PRESETS, "code")]
        if bad:
            raise ValueError(f"unknown preset(s): {', '.join(bad)}")
        h = Harness(config, store, presets=ps, models=[m.strip() for m in models.split(",")] if models else None,
                    check_facts=check, budget_usd=budget, parallel=parallel, sc_temperature=sc_temperature,
                    progress=(lambda s: typer.echo(s, err=True)) if fmt != "json" else None)
    except (KeyError, ValueError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(3)
    paid = [m for m in h.council if not (config.model(m).model.startswith("mock/") or config.model(m).is_local)]
    if paid and not yes:
        typer.confirm(f"{len(tasks)} tasks × ({len(ps or [1, 2])} presets + {len(h.council)} single models + "
                      f"self-consistency samples) on paid models ({', '.join(paid)}). Continue?", abort=True)
    rep = asyncio.run(h.run(tasks, suite))
    text = json.dumps(rep, ensure_ascii=False, indent=2) if fmt == "json" else to_markdown(rep)
    typer.echo(text)
    if out:
        from pathlib import Path

        Path(out).write_text(text, encoding="utf-8")
    if rep.get("status") != "done":
        raise typer.Exit(3)
    raise typer.Exit(0 if all(v["beats_baselines"] for v in rep["verdicts"].values()) else 1)


@eval_app.command("list")
def eval_list(limit: int = 20) -> None:
    """Past evals, newest first."""
    config = load_config()
    for body in RunStore(config.storage.resolved).list_evals(limit):
        r = json.loads(body)
        verdicts = " ".join(f"{p}{'✓' if v['beats_baselines'] else '✗'}" for p, v in (r.get("verdicts") or {}).items())
        typer.echo(f"{r['id']}  {r['created_at'][:16]}  {r['status']:<8} {r['suite']:<12} {r.get('tasks', 0):>3} tasks  "
                   f"{','.join(r.get('council') or [])}  {verdicts}")


@eval_app.command("show")
def eval_show(eval_id: str, fmt: str = typer.Option("md", "--format", "-f", help="md | json")) -> None:
    from .eval import to_markdown

    config = load_config()
    body = RunStore(config.storage.resolved).get_eval(eval_id)
    if body is None:
        typer.secho(f"not found: {eval_id}", fg="red", err=True)
        raise typer.Exit(1)
    typer.echo(body if fmt == "json" else to_markdown(json.loads(body)))


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Bind address. Keep 127.0.0.1 unless you know why."),
    port: int = typer.Option(6967),
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
