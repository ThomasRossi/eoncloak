# Contributing to eoncloak

Thanks for helping. eoncloak handles personal data, so a few rules below matter more than
in most projects. Please read the whole page once.

## Ground rules

- **Never share real personal data.** Not in issues, pull requests, test fixtures, logs or
  screenshots. Use invented names and the reserved examples: `example.com` emails,
  fictional phone ranges (e.g. `+44 20 7946 0xxx`, `555-01xx`), test IBANs.
- **Never commit mapping files** (`*.map.json`) or anonymisation output (`*.anon.*`), even
  from synthetic data. The `.gitignore` excludes them; don't force-add them.
- **A missed entity is a bug.** If eoncloak leaves personal data in an output, open an issue
  with a *synthetic* text that reproduces it. If the leak is systematic (e.g. a whole field
  of DOCX is skipped), follow [SECURITY.md](SECURITY.md) instead.
- Be kind and constructive. We follow the
  [Contributor Covenant](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).

## Development setup

We use [uv](https://docs.astral.sh/uv/).

Fork [ThomasRossi/eoncloak](https://github.com/ThomasRossi/eoncloak), then:

```bash
git clone https://github.com/<your-username>/eoncloak.git
cd eoncloak
git remote add upstream https://github.com/ThomasRossi/eoncloak.git
uv sync                       # creates .venv with runtime + dev dependencies
uv run eoncloak --help
```

Run the checks that CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests never call a real LLM: they use a fake model (see `tests/conftest.py`). If you want to
try against a real one, use a local server (`eoncloak check --provider ollama --model ...`)
and synthetic documents only.

## Making a change

1. Open an issue first for anything larger than a small fix, so we can agree on the
   approach.
2. Create a branch from `main` (`fix/docx-footnotes`, `feat/odt-input`, ...).
3. Add or update tests. Every bug fix comes with a test that fails without it.
4. Keep the code style: `ruff format`, type hints, short docstrings on public functions.
5. Update `README.md` when behaviour, options or formats change.
6. Open a pull request describing **what** changed and **why**, and how you tested it.

Commit messages: a short imperative summary line (`Fix phone detection for local formats`),
then details if needed.

## Project layout

```
src/eoncloak/
  cli.py          command line (typer)
  config.py       settings, provider profiles, --local-only guard
  llm.py          OpenAI-compatible client, structured-output fallbacks
  detect.py       language detection, chunking, LLM entity detection, prompts
  rules.py        regex/validator detectors (email, link, phone, IBAN)
  entities.py     entity types, the registry that keeps labels consistent, mapping files
  replace.py      finding and replacing surface forms
  anonymizer.py   the pipeline and the public Anonymizer API
  documents/      loaders and writers per format (text, word, pdf, mail)
tests/
  msgbuilder.py   writes synthetic Outlook .msg files, so no real emails are needed
```

### Benchmarking models

`scripts/benchmark.py` runs several models over a folder of documents and reports recall
against hand-checked answer keys, missed and extra values, time and tokens. Keep the
documents in `private/` (ignored by git) and API keys in `.env` (copy `.env.example`). See
the docstring at the top of the script. Useful options: `--glob "*.txt"` to pick documents,
`--bootstrap-expected` to draft answer keys, `--rescore runs/<timestamp>` to score saved
outputs again after editing a key, `--quantizations fp8,bf16` to avoid 4-bit OpenRouter
hosts, and a `#off` suffix on a model (`-m openrouter:qwen/qwen3.8-27b#off`) to turn its
thinking off. Never commit documents, answer keys or mapping files: they hold real data.

### Design principles

- **The LLM detects, the code replaces.** Never let a model rewrite document text.
- **Recall over precision.** Over-anonymising is annoying; under-anonymising is a leak.
- **Every provider is OpenAI-compatible.** Add a profile in `config.PROFILES` rather than a
  new client. Provider-specific options belong in `LLMClient._request_extras`.
- **Language-agnostic.** No English-only heuristics in detection or replacement; check that
  new logic works for scripts without spaces (Chinese, Japanese, Thai).
- **The UI is separate.** The planned web UI must use only the public Python API.

### Common contributions

- **A new input format:** add a class in `documents/` implementing `text()`, `plain_text()`
  and `apply()` and register it in `documents/__init__.py`. `text()` must return *all*
  text that could hold personal data (metadata included), because the leak check relies on
  it.
- **A new entity type:** add it to `EntityType`, describe it in `detect.TYPE_GUIDE`, and
  add a rule in `rules.py` if it has a fixed, checkable format.
- **Prompt changes:** include before/after results on a few synthetic documents in more
  than one language in the PR description.

## Licence

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
