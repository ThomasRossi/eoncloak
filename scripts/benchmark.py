"""Compare models on your own documents.

Folder layout (default root ``private/benchmark``, which git ignores):

    docs/                 documents to anonymise, any supported format
    expected/             optional answer keys: ``<document file name>.txt`` with one original
                          value per line that must not survive anonymisation (# for comments)
    runs/<timestamp>/     per-model outputs and mappings, results.json, report.md

Models are given as ``provider:model``, optionally followed by ``@base_url`` and by
``#reasoning`` (off, low, medium, high) to set the model's thinking:

    uv run python scripts/benchmark.py \\
        -m openrouter:anthropic/claude-haiku-5.5 \\
        -m openrouter:google/gemini-3.6-flash \\
        -m openrouter:qwen/qwen3.8-27b#off \\
        -m ollama:qwen3:14b \\
        -m vllm:Qwen/Qwen3-32B@http://dgx.lan:8000/v1

``--glob "*.txt"`` restricts the run to matching documents. ``--bootstrap-expected`` writes a
draft answer key for every document that has none, from the first model's findings. Review it
(delete false positives, add what was missed) before trusting the recall numbers: against its
own draft the first model always scores 100%.

After editing answer keys, ``--rescore runs/<timestamp>`` scores an earlier run's saved outputs
again without calling any model.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from eoncloak import Anonymizer, Registry, load_mapping, load_settings, save_mapping
from eoncloak.llm import LLMClient
from eoncloak.replace import Replacer

err = Console(stderr=True)
SUPPORTED = {".txt", ".md", ".docx", ".pdf", ".eml", ".msg"}


@dataclass
class Run:
    model: str
    document: str
    seconds: float = 0.0
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    entities: dict[str, int] = field(default_factory=dict)
    expected: int | None = None
    missed: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)
    truncated: int = 0
    providers: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def recall(self) -> float | None:
        if not self.expected:
            return None
        return 1 - len(self.missed) / self.expected


def parse_model(spec: str) -> tuple[str, str, str | None, str | None]:
    spec, _, reasoning = spec.partition("#")
    base_url = None
    if "@" in spec and spec.rsplit("@", 1)[1].startswith("http"):
        spec, base_url = spec.rsplit("@", 1)
    provider, _, model = spec.partition(":")
    if not model:
        raise typer.BadParameter(f"{spec!r}: expected provider:model")
    return provider, model, base_url, reasoning or None


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def read_expected(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def run_one(
    spec: str, document: Path, out_dir: Path, quantizations: list[str] | None = None
) -> tuple[Run, str, Registry | None]:
    """Anonymise one document; returns the run, the output text and the registry."""
    provider, model, base_url, reasoning = parse_model(spec)
    run = Run(spec, document.name)
    try:
        settings = load_settings(
            provider=provider,
            model=model,
            base_url=base_url,
            reasoning=reasoning,
            openrouter_quantizations=quantizations if provider == "openrouter" else None,
        )
        client = LLMClient(settings.llm)
        anonymizer = Anonymizer(settings, llm=client)
        start = time.perf_counter()
        result = anonymizer.anonymize_file(document, out_dir / f"{document.name}.anon.txt")
        run.seconds = round(time.perf_counter() - start, 2)
        save_mapping(anonymizer.registry, out_dir / f"{document.name}.map.json")
    except Exception as exc:  # keep benchmarking the other models/documents
        run.error = f"{type(exc).__name__}: {exc}"
        return run, "", None

    run.requests = client.usage.requests
    run.prompt_tokens = client.usage.prompt_tokens
    run.completion_tokens = client.usage.completion_tokens
    run.truncated = client.usage.truncated
    run.providers = dict(client.usage.providers)
    run.leaks = result.leaks
    run.entities = dict(Counter(e.type.value for e in anonymizer.registry.entities))
    return run, result.output.read_text(encoding="utf-8"), anonymizer.registry


def score(run: Run, output: str, registry: Registry, expected: list[str]) -> None:
    run.expected = len(expected)
    run.missed = [value for value in expected if Replacer({value: ""}).find(output)]
    wanted = {value.casefold() for value in expected}
    run.extra = sorted(
        alias for entity in registry.entities for alias in entity.aliases
        if alias.casefold() not in wanted
    )  # fmt: skip


def bootstrap(expected_path: Path, registry: Registry, spec: str) -> None:
    lines = [
        f"# Draft answer key generated from {spec}.",
        "# Review it: delete values that are not personal data, add anything that was missed.",
    ]
    for entity in registry.entities:
        lines.append(f"# {entity.label}")
        lines += sorted(entity.aliases)
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def write_report(runs: list[Run], models: list[str], path: Path) -> None:
    lines = ["# eoncloak benchmark", "", f"Run: {path.parent.name}", "", "## Summary", ""]
    lines += [
        "| Model | Mean recall | Missed | Extra | Errors | Seconds | Requests | Tokens in / out |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model in models:
        mine = [r for r in runs if r.model == model]
        recalls = [r.recall for r in mine if r.recall is not None]
        mean = sum(recalls) / len(recalls) if recalls else None
        lines.append(
            f"| `{model}` | {pct(mean)} | {sum(len(r.missed) for r in mine)} "
            f"| {sum(len(r.extra) for r in mine)} | {sum(r.error is not None for r in mine)} "
            f"| {sum(r.seconds for r in mine):.1f} | {sum(r.requests for r in mine)} "
            f"| {sum(r.prompt_tokens for r in mine)} / {sum(r.completion_tokens for r in mine)} |"
        )
    lines += ["", "## Per document", ""]
    lines += [
        "| Document | Model | Recall | Entities | Seconds | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for run in sorted(runs, key=lambda r: (r.document, models.index(r.model))):
        entities = ", ".join(f"{n} {t}" for t, n in sorted(run.entities.items()))
        notes = run.error or ("LEAK CHECK FAILED" if run.leaks else "")
        if run.truncated:
            notes += f" {run.truncated} reply(ies) cut off at the token cap"
        if run.providers:
            notes += " hosts: " + ", ".join(f"{p} {n}" for p, n in sorted(run.providers.items()))
        lines.append(
            f"| {run.document} | `{run.model}` | {pct(run.recall)} | {entities} "
            f"| {run.seconds} | {notes} |"
        )
    lines += ["", "## Details", ""]
    for run in runs:
        if run.missed or run.extra:
            lines.append(f"### {run.document} — `{run.model}`")
            if run.missed:
                lines.append("Missed (still in the output): " + "; ".join(run.missed))
            if run.extra:
                lines.append("Extra (anonymised, not in the answer key): " + "; ".join(run.extra))
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def rescore(root: Path, run_dir: Path) -> None:
    runs = []
    for data in json.loads((run_dir / "results.json").read_text(encoding="utf-8")):
        data.pop("recall", None)
        run = Run(**data)
        out_dir = run_dir / slug(run.model)
        output = out_dir / f"{run.document}.anon.txt"
        expected_path = root / "expected" / f"{run.document}.txt"
        if run.error is None and output.exists() and expected_path.exists():
            registry = load_mapping(out_dir / f"{run.document}.map.json")
            score(run, output.read_text(encoding="utf-8"), registry, read_expected(expected_path))
        runs.append(run)
    models = list(dict.fromkeys(r.model for r in runs))
    write_results(runs, run_dir)
    write_report(runs, models, run_dir / "report.md")
    err.print(f"[green]Report:[/] {run_dir / 'report.md'}")


def write_results(runs: list[Run], run_dir: Path) -> None:
    (run_dir / "results.json").write_text(
        json.dumps([asdict(r) | {"recall": r.recall} for r in runs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main(
    models: Annotated[
        list[str] | None, typer.Option("--model", "-m", help="provider:model[@url][#reasoning]")
    ] = None,
    root: Annotated[Path, typer.Option(help="Benchmark folder.")] = Path("private/benchmark"),
    bootstrap_expected: Annotated[
        bool, typer.Option(help="Draft missing answer keys from the first model.")
    ] = False,
    glob: Annotated[str, typer.Option(help="Only documents matching this pattern.")] = "*",
    quantizations: Annotated[
        str | None,
        typer.Option(help='OpenRouter: only hosts with these quantizations, e.g. "fp8,bf16".'),
    ] = None,
    rescore_run: Annotated[
        Path | None,
        typer.Option("--rescore", help="Score an earlier run directory again; no model calls."),
    ] = None,
) -> None:
    if rescore_run is not None:
        rescore(root, rescore_run)
        return
    if not models:
        err.print("[red]Give at least one --model[/]")
        raise typer.Exit(1)
    documents = sorted(p for p in (root / "docs").glob(glob) if p.suffix.lower() in SUPPORTED)
    if not documents:
        err.print(f"[red]No documents in {root / 'docs'}[/]")
        raise typer.Exit(1)
    run_dir = root / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    quant_list = [q.strip() for q in quantizations.split(",")] if quantizations else None
    runs: list[Run] = []

    for i, spec in enumerate(models):
        out_dir = run_dir / slug(spec)
        out_dir.mkdir(parents=True, exist_ok=True)
        for document in documents:
            expected_path = root / "expected" / f"{document.name}.txt"
            run, output, registry = run_one(spec, document, out_dir, quant_list)
            if registry is not None:
                if i == 0 and bootstrap_expected and not expected_path.exists():
                    bootstrap(expected_path, registry, spec)
                    err.print(f"  drafted answer key {expected_path}")
                if expected_path.exists():
                    score(run, output, registry, read_expected(expected_path))
            runs.append(run)
            status = run.error or f"recall {pct(run.recall)}, {run.seconds}s"
            err.print(f"{spec} · {document.name}: {status}")

    write_results(runs, run_dir)
    write_report(runs, models, run_dir / "report.md")
    err.print(f"[green]Report:[/] {run_dir / 'report.md'}")


if __name__ == "__main__":
    typer.run(main)
