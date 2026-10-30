"""Command line interface: ``eoncloak anonymize | restore | check | init``."""

from __future__ import annotations

import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from . import __version__
from .anonymizer import (
    Anonymizer,
    default_output_path,
    load_mapping,
    restore_document,
    save_mapping,
)
from .config import (
    EXAMPLE_CONFIG,
    PROFILES,
    ConfigError,
    Settings,
    check_local_only,
    is_local_url,
    load_settings,
)
from .documents import TextDocument, UnsupportedFormat, load_document, save_document
from .entities import EntityType, Registry
from .llm import LLMClient, LLMError

app = typer.Typer(
    help="Anonymise documents (text, Markdown, DOCX, PDF) with a cloud or local LLM.",
    no_args_is_help=True,
    add_completion=False,
)
err = Console(stderr=True)

STDIN = Path("-")


class OutputFormat(StrEnum):
    txt = "txt"
    md = "md"
    docx = "docx"


ConfigOpt = Annotated[
    Path | None, typer.Option("--config", "-c", help="Config file (default: ./eoncloak.toml).")
]
ProviderOpt = Annotated[
    str | None, typer.Option(help=f"LLM provider: {', '.join(PROFILES)}.", show_default=False)
]
ModelOpt = Annotated[str | None, typer.Option(help="Model name.", show_default=False)]
BaseUrlOpt = Annotated[
    str | None, typer.Option(help="OpenAI-compatible endpoint, e.g. http://dgx:8000/v1.")
]
LocalOnlyOpt = Annotated[
    bool | None,
    typer.Option(
        "--local-only/--allow-remote",
        help="Refuse to send text anywhere but this machine or a private network.",
        show_default=False,
    ),
]


def _fail(message: str) -> typer.Exit:
    err.print(f"[bold red]error:[/] {message}")
    return typer.Exit(1)


def _settings(config: Path | None, **overrides) -> Settings:
    try:
        return load_settings(config, **overrides)
    except (ConfigError, ValueError) as exc:
        raise _fail(str(exc)) from exc


def _warn_if_remote(settings: Settings) -> None:
    if not settings.anonymize.use_llm:
        return
    url = settings.llm.resolved_base_url()
    if not is_local_url(url):
        err.print(
            f"[yellow]Note:[/] document text will be sent to [bold]{url}[/]. "
            "Use --local-only with a local provider to keep it on your network."
        )


def _version(value: bool) -> None:
    if value:
        print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool, typer.Option("--version", callback=_version, is_eager=True, help="Show version.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose logging.")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=err, show_time=False, show_path=False)],
    )
    for noisy in ("httpx", "openai", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.INFO if verbose else logging.WARNING)


@app.command()
def anonymize(
    inputs: Annotated[list[Path], typer.Argument(help="Files to anonymise, or - for stdin.")],
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", "-o", help="Where to write results.")
    ] = None,
    to: Annotated[
        OutputFormat | None,
        typer.Option(help="Output format (default: same as input; PDF becomes txt)."),
    ] = None,
    mapping: Annotated[
        Path | None,
        typer.Option(
            "--mapping",
            "-m",
            help="Mapping file to reuse and update. Contains the original data: keep it safe.",
        ),
    ] = None,
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    base_url: BaseUrlOpt = None,
    local_only: LocalOnlyOpt = None,
    no_llm: Annotated[
        bool, typer.Option("--no-llm", help="Rules only (emails, phones, IBANs); no LLM.")
    ] = False,
    language: Annotated[
        str | None, typer.Option(help="ISO 639-1 code; default: detected automatically.")
    ] = None,
    region: Annotated[
        str | None, typer.Option(help="ISO 3166-1 code for phone numbers, e.g. IT.")
    ] = None,
    entities: Annotated[
        str | None,
        typer.Option(help=f"Comma-separated types to anonymise ({', '.join(EntityType)})."),
    ] = None,
    config: ConfigOpt = None,
) -> None:
    """Anonymise documents, giving the same label to every mention of the same entity."""
    entity_list = None
    if entities:
        try:
            entity_list = [EntityType(e.strip().upper()) for e in entities.split(",") if e.strip()]
        except ValueError as exc:
            raise _fail(str(exc)) from exc
    settings = _settings(
        config,
        provider=provider,
        model=model,
        base_url=base_url,
        local_only=local_only,
        use_llm=False if no_llm else None,
        language=language,
        region=region,
        entities=entity_list,
    )

    try:
        registry = None
        if mapping and mapping.exists():
            registry = load_mapping(mapping)
            if registry.label_format != settings.anonymize.label_format:
                err.print(f"[yellow]Note:[/] using label format from {mapping}")
        anonymizer = Anonymizer(settings, registry=registry)
        _warn_if_remote(settings)
    except (ConfigError, LLMError, ValueError) as exc:
        raise _fail(str(exc)) from exc

    leaked = False
    for source in inputs:
        try:
            if source == STDIN:
                result = anonymizer.anonymize_text(sys.stdin.read())
                sys.stdout.write(result.text)
            else:
                output = _output_path(source, output_dir, to)
                with err.status(f"Anonymising {source} ..."):
                    result = anonymizer.anonymize_file(source, output)
                err.print(
                    f"[green]✓[/] {source} → {output}  (language: {result.language.language})"
                )
        except (OSError, UnsupportedFormat, LLMError, ValueError) as exc:
            raise _fail(f"{source}: {exc}") from exc
        if result.leaks:
            leaked = True
            err.print(
                f"[bold red]Leak check failed[/] for {source}: {len(result.leaks)} original "
                "value(s) still present in the output. Please report this as a bug."
            )

    _print_summary(anonymizer.registry)
    if mapping:
        save_mapping(anonymizer.registry, mapping)
        err.print(f"Mapping written to {mapping} (contains the original data: keep it private)")
    if leaked:
        raise typer.Exit(2)


def _output_path(source: Path, output_dir: Path | None, to: OutputFormat | None) -> Path:
    path = default_output_path(source, f".{to.value}" if to else None)
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / path.name
    return path


def _print_summary(registry: Registry) -> None:
    counts: dict[str, int] = {}
    for entity in registry.entities:
        counts[entity.type.value] = counts.get(entity.type.value, 0) + 1
    if counts:
        summary = ", ".join(f"{n} {t}" for t, n in sorted(counts.items()))
        err.print(f"Entities: {summary}")
    else:
        err.print("[yellow]No personal data found.[/]")


@app.command()
def restore(
    input: Annotated[Path, typer.Argument(help="Anonymised file, or - for stdin.")],
    mapping: Annotated[Path, typer.Option("--mapping", "-m", help="Mapping file.")],
    output: Annotated[Path | None, typer.Option("--output", "-o", help="Output file.")] = None,
) -> None:
    """Put the original values back into an anonymised document using its mapping file."""
    try:
        registry = load_mapping(mapping)
        if input == STDIN:
            document = TextDocument(sys.stdin.read())
            restore_document(document, registry)
            sys.stdout.write(document.text())
            return
        document = load_document(input)
        restore_document(document, registry)
        if output is None:
            stem = input.stem.removesuffix(".anon")
            output = input.with_name(f"{stem}.restored{input.suffix}")
        save_document(document, output)
    except (OSError, UnsupportedFormat, ValueError, KeyError) as exc:
        raise _fail(str(exc)) from exc
    err.print(f"[green]✓[/] {input} → {output}")


@app.command()
def check(
    provider: ProviderOpt = None,
    model: ModelOpt = None,
    base_url: BaseUrlOpt = None,
    local_only: LocalOnlyOpt = None,
    config: ConfigOpt = None,
) -> None:
    """Show the effective LLM configuration and test the connection with a short sample."""
    settings = _settings(
        config, provider=provider, model=model, base_url=base_url, local_only=local_only
    )
    llm_settings = settings.llm
    url = llm_settings.resolved_base_url()
    table = Table(show_header=False, box=None)
    table.add_row("provider", llm_settings.provider)
    table.add_row("endpoint", url)
    table.add_row("location", "local / private network" if is_local_url(url) else "remote")
    table.add_row("local_only", str(settings.anonymize.local_only))
    err.print(table)

    sample = (
        "Dear Ms. Jane Doe, please call Acme Corp. at +44 20 7946 0958 or write to "
        "jane.doe@example.com. Jane lives at 221B Baker Street, London NW1 6XE."
    )
    try:
        check_local_only(settings)
        client = LLMClient(llm_settings)
        err.print(f"model     {client.model}")
        anonymizer = Anonymizer(settings, llm=client)
        with err.status("Running a sample anonymisation ..."):
            result = anonymizer.anonymize_text(sample)
    except (ConfigError, LLMError) as exc:
        raise _fail(str(exc)) from exc
    err.print(f"structured output mode: {client.structured_output_mode}")
    err.print(f"[dim]{sample}[/]")
    err.print(result.text)
    err.print("[green]✓ the provider works[/]")


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Where to write the config.")] = Path(
        "eoncloak.toml"
    ),
    force: Annotated[bool, typer.Option(help="Overwrite an existing file.")] = False,
) -> None:
    """Write an example configuration file."""
    if path.exists() and not force:
        raise _fail(f"{path} exists; use --force to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    err.print(f"[green]✓[/] wrote {path}")
