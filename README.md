# eoncloak

[![CI](https://github.com/ThomasRossi/eoncloak/actions/workflows/ci.yml/badge.svg)](https://github.com/ThomasRossi/eoncloak/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Anonymise documents with AI: names of people, companies, addresses, phone numbers, emails,
IBANs and personal identifiers are replaced with consistent labels, so the same person is
`[PERSON_1]` everywhere in a document, and across a set of documents too.

```text
Mario Rossi (mario.rossi@acme.it) signed for ACME S.p.A. Rossi will call +39 02 1234 5678.
        ↓
[PERSON_1] ([EMAIL_1]) signed for [ORG_1]. [PERSON_1] will call [PHONE_1].
```

- **Any language.** The main language is detected automatically (defaults to English).
- **Cloud or local LLM, one switch.** OpenRouter by default; Ollama, llama.cpp, vLLM, LM
  Studio, NVIDIA NIM or any other OpenAI-compatible server (a DGX, a workstation, a lab
  box) with `--provider` / `--base-url`. `--local-only` guarantees text never leaves your
  network.
- **Formats.** Input: `.txt`, `.md`, `.docx`, `.pdf` (text-based). Output: plain text,
  Markdown or `.docx`. DOCX → DOCX keeps formatting, headers, footers, tables, comments.
- **Reversible if you want.** An optional mapping file lets you restore the originals.

> **Status: alpha.** Always review the output before sharing it. No automatic tool catches
> 100% of personal data.

## Install

```bash
pip install eoncloak          # or: uv tool install eoncloak / pipx install eoncloak
```

Requires Python 3.11+.

## Quick start

```bash
# Cloud (default): OpenRouter
export OPENROUTER_API_KEY=sk-or-...
eoncloak anonymize contract.docx            # → contract.anon.docx

# Local: Ollama on this machine
eoncloak anonymize contract.pdf --provider ollama --model qwen3:14b --local-only
                                            # → contract.anon.txt

# Local: vLLM on a DGX on your network
eoncloak anonymize *.docx --provider vllm --base-url http://dgx.lan:8000/v1 --local-only -o out/

# Same labels across a case file, and restore later
eoncloak anonymize case/*.pdf --to docx --mapping case42.map.json
eoncloak restore out/report.anon.docx --mapping case42.map.json

# Pipes
cat notes.txt | eoncloak anonymize - > notes.anon.txt

# No LLM at all: emails, phone numbers and IBANs only
eoncloak anonymize notes.txt --no-llm
```

Check a provider before using it:

```bash
eoncloak check --provider ollama --model qwen3:14b
```

## Providers

Every provider is reached through the OpenAI-compatible `/v1/chat/completions` API, which
all common local servers implement. A profile only sets the default address:

| `--provider` | Default endpoint | Notes |
|---|---|---|
| `openrouter` (default) | `https://openrouter.ai/api/v1` | Needs `OPENROUTER_API_KEY`. Default model `anthropic/claude-haiku-5.5`. Only routes to providers that don't store prompts. |
| `ollama` | `http://localhost:11434/v1` | Easiest local setup. |
| `llamacpp` | `http://localhost:8080/v1` | `llama-server`; good with long contexts. |
| `vllm` | `http://localhost:8000/v1` | Best throughput on GPUs / DGX. |
| `lmstudio` | `http://localhost:1234/v1` | Desktop app's local server. |
| `custom` | set `--base-url` | NVIDIA NIM, SGLang, a company gateway, … |

If no model is given for a local server that serves exactly one model, that model is used.
API keys can also be given via `EONCLOAK_API_KEY`.

**`--local-only`** resolves the endpoint and refuses to run unless every address is on
loopback or a private network (10/8, 172.16/12, 192.168/16, Tailscale's 100.64/10, IPv6
ULA). Without it, eoncloak prints a notice whenever text goes to a remote endpoint.

Which local model? Larger is better at spotting names; 8–14B instruct models (Qwen, Gemma,
Llama, Mistral families) work well, 30B+ is better on long or messy documents. Try yours on
a sample with `eoncloak check` and a real (non-sensitive) document.

## Configuration

`eoncloak init` writes an `eoncloak.toml` with every option. Settings are read from
`./eoncloak.toml`, then `~/.config/eoncloak/config.toml`; environment variables
(`EONCLOAK_PROVIDER`, `EONCLOAK_MODEL`, `EONCLOAK_BASE_URL`, `EONCLOAK_LOCAL_ONLY`,
`EONCLOAK_LANGUAGE`) override the file, and command line flags override both.

```toml
[llm]
provider = "vllm"
base_url = "http://dgx.lan:8000/v1"
model = "Qwen/Qwen3-32B"

[anonymize]
local_only = true
entities = ["PERSON", "ORG", "ADDRESS", "EMAIL", "PHONE", "IBAN", "ID"]
label_format = "[{type}_{n}]"
```

## Python API

```python
from eoncloak import Anonymizer, load_settings, save_mapping

anonymizer = Anonymizer(load_settings(provider="ollama", model="qwen3:14b", local_only=True))
result = anonymizer.anonymize_file("contract.docx")  # writes contract.anon.docx
print(result.output, result.language.language, result.leaks)

text = anonymizer.anonymize_text("Mario Rossi wrote to mario@rossi.it").text
save_mapping(anonymizer.registry, "case.map.json")
```

## How it works

1. **Extract** the text (DOCX: body, tables, headers, footers, footnotes, comments,
   tracked changes, hyperlinks, metadata; PDF: text layer).
2. **Detect language** with one short LLM call (or `--language`).
3. **Detect entities**: rules for emails, phone numbers and IBANs; the LLM for names,
   organisations, addresses and identifiers. Long documents are processed in chunks, and
   entities found so far are passed to each chunk, so "Mario Rossi", "Rossi" and "M. Rossi"
   are linked to the same person.
4. **Replace in code, not by the LLM.** The LLM only *lists* what it finds; eoncloak then
   replaces every occurrence of every known form itself. The rest of the text is never
   rewritten, and a mention the model missed in one chunk is still caught if it was found
   in another.
5. **Leak check**: the written output is scanned again for any original value.

## Limitations

- Scanned PDFs (images without a text layer) need OCR, which is not supported yet.
- PDF output is not re-rendered as PDF: you get text, Markdown or DOCX.
- DOCX: text inside images, charts and SmartArt is not processed.
- Restoring maps each label back to the entity's fullest name ("Rossi" comes back as
  "Mario Rossi").
- Mapping files contain the original personal data. Store them like the originals.

## Roadmap

- [ ] Re-render anonymised PDFs as PDF (in-place redaction)
- [ ] OCR for scanned PDFs
- [ ] Offline detector without an LLM server (e.g. GLiNER) for names
- [ ] Evaluation set and per-model benchmark
- [ ] Phase 2: a small local web UI (separate package, built on the Python API)

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md), and
[SECURITY.md](SECURITY.md) for reporting vulnerabilities or leaks.

## License

[MIT](LICENSE)
