# eoncloak

[![CI](https://github.com/ThomasRossi/eoncloak/actions/workflows/ci.yml/badge.svg)](https://github.com/ThomasRossi/eoncloak/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Anonymise confidential documents with AI, without them leaving your network.**

Built for legal, compliance and investigation work, where files are covered by professional
secrecy and GDPR. eoncloak runs on small local models and replaces people, companies,
addresses, emails, phone numbers, links, IBANs, dates of birth and identifiers of people and
companies (tax codes, company and VAT numbers, ...) with consistent labels: the same person
is `[PERSON_1]` everywhere in a document, and across a set of documents.

```text
John Doe (born 12 March 1961, john.doe@example.com) is director of Acme Trading Ltd,
company no. 01234567, VAT GB123456789. Doe will call +44 20 7946 0958.
        ↓
[PERSON_1] (born [BIRTH_DATE_1], [EMAIL_1]) is director of [ORG_1],
company no. [ID_2], VAT [ID_1]. [PERSON_1] will call [PHONE_1].
```

## Quick start

```bash
pip install eoncloak          # or: uv tool install eoncloak / pipx install eoncloak
eoncloak anonymize contract.docx --provider ollama --model qwen3:14b --local-only
                              # → contract.anon.docx
```

Requires Python 3.11+ and, for local use, a model server such as
[Ollama](https://ollama.com). More examples [below](#usage).

## Features

- **Local first.** Works with Ollama, llama.cpp, vLLM, LM Studio, NVIDIA NIM or any other
  OpenAI-compatible server (a DGX, a workstation, a lab box). `--local-only` guarantees
  text never leaves your network. Cloud models through OpenRouter are also supported, and
  are what eoncloak uses when no provider is configured: set `provider` and `local_only`
  once in [`eoncloak.toml`](#configuration) to make local the default.
- **Any language.** The main language is detected automatically, with English as the
  fallback when detection fails.
- **Formats.** Input: `.txt`, `.md`, `.docx`, `.pdf` (text-based), emails (`.eml`, Outlook
  `.msg`). Output: plain text, Markdown or `.docx`. DOCX → DOCX keeps formatting, headers,
  footers, tables and comments.
- **Reversible if you want.** An optional mapping file lets you restore the originals.
  While the mapping exists, the output is pseudonymised, not anonymised, data under GDPR:
  it is still personal data, so keep the mapping as secure as the originals.
- **Consistent labels, by choice.** Documents processed in one run, or with the same
  mapping file, share labels, so `[PERSON_1]` is the same person in all of them. That
  keeps a case file readable, but it also makes the documents linkable to each other;
  process them separately when that matters.

> **Status: alpha.** Always review the output before sharing it. No automatic tool catches
> 100% of personal data, and contextual details eoncloak leaves in place (dates, places,
> nationality, job titles, case references) can still identify people. See
> [Limitations](#limitations).

## Usage

```bash
# Local: Ollama on this machine
eoncloak anonymize contract.pdf --provider ollama --model qwen3:14b --local-only
                                            # → contract.anon.txt

# Local: vLLM on a DGX on your network
eoncloak anonymize *.docx --provider vllm --base-url http://dgx.lan:8000/v1 --local-only -o out/

# Cloud: OpenRouter, key in ./.env (see below)
echo 'OPENROUTER_API_KEY=sk-or-...' >> .env
eoncloak anonymize contract.docx            # → contract.anon.docx

# Emails (Outlook .msg or .eml) → text, or --to docx / --to md
eoncloak anonymize inbox/*.msg -o anonymised/

# Same labels across a case file, and restore later
eoncloak anonymize case/*.pdf --to docx --mapping case42.map.json
eoncloak restore out/report.anon.docx --mapping case42.map.json

# Pipes
cat notes.txt | eoncloak anonymize - > notes.anon.txt

# No LLM at all: emails, links, phone numbers, IBANs, VAT numbers and labelled ids only
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

**API keys and `.env`.** eoncloak reads a `.env` file in the current directory (copy
[`.env.example`](.env.example)), so keys don't have to be exported in your shell. Variables
already set in the environment take precedence over the file, and the file is never
loaded into the process environment. `.env` is in `.gitignore`; keep keys out of
`eoncloak.toml`.

**`--local-only`** resolves the endpoint and refuses to run unless every address is on
loopback or a private network (10/8, 172.16/12, 192.168/16, Tailscale's 100.64/10, IPv6
ULA). Without it, eoncloak prints a notice whenever text goes to a remote endpoint.

**Thinking models.** Reasoning models (Qwen3, gpt-oss, ...) may think before answering,
which is billed and slow but rarely finds more names. `--reasoning off` (or `reasoning =
"off"` in the config) turns it off: on OpenRouter through its `reasoning` parameter, on
vLLM, SGLang and llama.cpp through `chat_template_kwargs.enable_thinking`. Each reply is
capped (`max_output_tokens`, 4096 with thinking off, otherwise 16384) so that a model stuck
repeating itself can't run for minutes; a reply cut off at the cap is retried from scratch.

**OpenRouter hosts.** Open models are served by many hosts, some at 4-bit precision. Set
`openrouter_quantizations = ["fp8", "bf16"]` to skip those (closed models report
`"unknown"`), and `openrouter_zdr = true` to allow only zero-data-retention hosts.

Which model? See [Benchmarks](#benchmarks). Try yours on a sample with `eoncloak check` and a
real (non-sensitive) document.

## Configuration

`eoncloak init` writes an `eoncloak.toml` with every option. Settings are read from
`./eoncloak.toml`, then `~/.config/eoncloak/config.toml`; environment variables
(`EONCLOAK_PROVIDER`, `EONCLOAK_MODEL`, `EONCLOAK_BASE_URL`, `EONCLOAK_REASONING`,
`EONCLOAK_LOCAL_ONLY`, `EONCLOAK_LANGUAGE`), from `./.env` or the shell, override the
file, and command line flags override both.

```toml
[llm]
provider = "vllm"
base_url = "http://dgx.lan:8000/v1"
model = "Qwen/Qwen3-32B"

[anonymize]
local_only = true
entities = ["PERSON", "ORG", "ADDRESS", "EMAIL", "PHONE", "IBAN", "ID", "URL", "BIRTH_DATE"]
label_format = "[{type}_{n}]"
```

## Python API

```python
from eoncloak import Anonymizer, load_settings, save_mapping

anonymizer = Anonymizer(load_settings(provider="ollama", model="qwen3:14b", local_only=True))
result = anonymizer.anonymize_file("contract.docx")  # writes contract.anon.docx
print(result.output, result.language.language, result.leaks)

text = anonymizer.anonymize_text("John Doe wrote to john.doe@example.com").text
save_mapping(anonymizer.registry, "case.map.json")
```

## How it works

1. **Extract** the text (DOCX: body, tables, headers, footers, footnotes, comments,
   tracked changes, hyperlinks, metadata; PDF: text layer; emails: From/To/Cc/Bcc/Reply-To,
   Date, Subject, attachment names and the body, plain or HTML).
2. **Detect language** with one short LLM call (or `--language`).
3. **Detect entities**: rules for emails, phone numbers, IBANs, VAT numbers, codici
   fiscali, identifiers written after a label ("Company no.: 01234567", "P.IVA ...",
   "HRB ...") and links (each whole link becomes `[URL_n]`, since links often carry names
   and account ids; links that PDF extraction wrapped at a hyphen are followed onto the
   next line); the LLM for names, organisations, addresses, identifiers and dates of
   birth. The model reads each distinct line once, so the signatures, disclaimers and
   quoted headers repeated in an email thread are not sent again (on a long thread this
   halves the tokens). Long documents are processed in chunks, and entities found so far
   are passed to each chunk, so "John Doe", "Doe" and "J. Doe" are linked to the same
   person. Shorter forms are added automatically: "Acme Inc." also covers "Acme", and
   "Northwind Ltd." also covers "Northwind Ltd".
4. **Replace in code, not by the LLM.** The LLM only *lists* what it finds; eoncloak then
   replaces every occurrence of every known form itself. The rest of the text is never
   rewritten, and a mention the model missed in one chunk is still caught if it was found
   in another.
5. **Leak check**: the written output is scanned again for any original value.

## Benchmarks

Accuracy on our internal test dataset: business documents in English and French (a report
and a long email thread), with 113 hand-checked values that must disappear (people,
organisations, addresses, emails, phone numbers, company and VAT numbers, other
identifiers, links, dates of birth). Recall is the share of those values gone from the
output. eoncloak `main`, October 2026, default settings, models reached through OpenRouter.

| Model | Recall | Missed | Time | Tokens in / out | Approx. cost |
|---|---|---|---|---|---|
| `anthropic/claude-haiku-5.5` (default) | 99.1% | 1 / 113 | 91 s | 44k / 20k | $0.015 |
| `google/gemma-4-26b-a4b-it` (open, 4B active) | 97.3% | 3 / 113 | 64 s | 26k / 6k | $0.004 |
| `qwen/qwen3.8-27b`, `--reasoning off` (open, dense) | 97.3% | 3 / 113 | 122 s | 24k / 7k | $0.01 |

- The misses are organisation names mentioned only in passing, or a first name alone.
  All three models also replace some things the test doesn't require, such as public
  authorities and product brands.
- With thinking on, Qwen3.8 27B was about 28 times slower on the report and found no
  more values; use `--reasoning off` for this task.
- The open models were restricted to fp8/bf16 hosts (`openrouter_quantizations`). Without
  that, some 4-bit hosts occasionally loop until the output cap, which turned one run
  from about 2 minutes into 20.
- Times are OpenRouter round trips and say little about local hardware. Gemma 4 26B-A4B
  uses about 4B parameters per token, so on a single GPU or DGX it should be considerably
  faster than the dense Qwen3.8 27B.

Run the same comparison on your own documents with `scripts/benchmark.py` (see
[CONTRIBUTING.md](CONTRIBUTING.md#benchmarking-models)); keep them in `private/`, which git
ignores.

## Limitations

- Scanned PDFs (images without a text layer) need OCR, which is not supported yet.
- Emails: attachments are listed by name but their contents are not anonymised; save them
  and run eoncloak on them separately. Routing headers (Received, Message-ID, X-…) are
  dropped from the output. Outlook messages whose body exists only as RTF are not supported
  yet.
- PDF output is not re-rendered as PDF: you get text, Markdown or DOCX.
- DOCX: text inside images, charts and SmartArt is not processed.
- Restoring maps each label back to the entity's fullest name ("Doe" comes back as
  "John Doe").
- Mapping files contain the original personal data. Store them like the originals.
- **Anonymised in the GDPR sense?** Not automatically. Under GDPR Recital 26 (and the UK
  ICO's "motivated intruder" test) data is anonymous only if nobody can re-identify people by
  means reasonably likely to be used, including public registers. eoncloak removes direct
  identifiers, but details it leaves in place can still single someone out when combined:
  a company's incorporation date and business activity codes, a nationality, a job title, a
  rare event. Review the output with that test in mind. While you keep the mapping file,
  the output is pseudonymised, not anonymised.

## Roadmap

- [ ] Re-render anonymised PDFs as PDF (in-place redaction)
- [ ] OCR for scanned PDFs
- [ ] Emails: anonymise attachments too; RTF-only Outlook bodies
- [ ] Offline detector without an LLM server (e.g. GLiNER) for names
- [ ] Evaluation set and per-model benchmark
- [ ] Phase 2: a small local web UI (separate package, built on the Python API)

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md), and
[SECURITY.md](SECURITY.md) for reporting vulnerabilities or leaks.

## License

[MIT](LICENSE)
