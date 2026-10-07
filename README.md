# genea: a genealogy knowledge-base agent

A local, open-source assistant for a genealogy research folder: GEDCOM family trees, documents
(txt, md, docx, pdf) and photo metadata (EXIF/IPTC/XMP). It answers questions with citations,
finds inconsistencies, writes research notes, and **proposes** corrections that you review and
apply. Point it at any folder; each folder is its own knowledge base.

## Install

1. [uv](https://docs.astral.sh/uv/): `winget install astral-sh.uv`
2. [Ollama](https://ollama.com), then pull a chat model and the embedding model:
   `ollama pull qwen3:14b` (use `qwen3:8b` with less than ~12 GB of GPU memory) and `ollama pull bge-m3`
3. [ExifTool](https://exiftool.org): `winget install OliverBetz.ExifTool`
   (if a Chocolatey install reports "Cannot find file at ...exiftool.exe", run `choco install exiftool --force`)
4. In this repository: `uv sync`, then check everything with `uv run genea doctor`

Optional cloud models: `uv sync --extra cloud`, set `ANTHROPIC_API_KEY`, and use
`model = "anthropic:claude-sonnet-5-5"`. If installing the `anthropic` package fails on Windows with
missing-module errors, enable long paths (`git config --system core.longpaths true` and the
`LongPathsEnabled` registry setting).

`genea ask` and `genea chat` need Ollama running with the configured chat model and `bge-m3` pulled
(or a cloud model with an API key and the optional `cloud` extra, see above). Indexing also needs
`bge-m3`; `person`, `conflicts`, `status` and `changes` work without any model. ExifTool is needed
to read image metadata and to write XMP sidecars. `genea doctor` diagnoses all of this.

## Quick start

```powershell
uv run genea --kb D:\Genealogy\Novak init      # creates D:\Genealogy\Novak\.genea\
uv run genea --kb D:\Genealogy\Novak index     # re-run any time; only changed files are processed
uv run genea --kb D:\Genealogy\Novak ask "Kdo byli prarodiče Jiřího Nováka?"
uv run genea --kb D:\Genealogy\Novak chat --show-tools
```

To skip `--kb`, name your knowledge bases in `~/.genea/config.toml`:

```toml
default_kb = "novak"
[kbs]
novak = 'D:\Genealogy\Novak'
svoboda = 'E:\Rodokmen\Svoboda'
```

Then `uv run genea ask "..."` or `uv run genea --kb svoboda ask "..."`. The `GENEA_KB` environment
variable works too.

## Commands

| Command | What it does |
|---|---|
| `genea doctor` | Check ExifTool, Ollama and the models |
| `genea init` / `index [--rebuild]` / `status` | Set up, index, and inspect a knowledge base |
| `genea ask "..."` / `chat` | Talk to the agent (`--show-tools` shows its tool calls) |
| `genea person <name or id>` | Look a person up without the LLM |
| `genea conflicts [id]` | Rule-based consistency checks without the LLM |
| `genea changes list / show / apply / reject / undo / export` | Review what the agent proposed |

## How changes work

The agent never edits your files. It writes research notes to `.genea/notes/` and **proposes**
changes to `.genea/changes/pending/`. Review them with `genea changes show <id>`.

- **Photo metadata** (title, description, keywords, people, place, date): `genea changes apply <id>`
  writes an XMP **sidecar** (`photo.xmp`) next to the photo. The photo itself is never touched,
  a backup is kept, and `genea changes undo <id>` restores it.
- **Family tree**: `genea changes export` writes a checklist and a GEDCOM fragment to
  `.genea/exports/`. Make the edits in your genealogy program, then mark each change with
  `genea changes apply <id> --manual`. Writing `.ged` files directly is planned for a later version.

## Configuration (`~/.genea/config.toml` or `<kb>/.genea/config.toml`)

| Key | Default | Meaning |
|---|---|---|
| `model` | `ollama:qwen3:14b` | Chat model (`ollama:<name>` or e.g. `anthropic:claude-sonnet-5-5`) |
| `embed_model` / `embed_dim` | `bge-m3` / `1024` | Local embedding model (changing it needs `index --rebuild`) |
| `ollama_url` | `http://localhost:11434` | Ollama server |
| `exclude` | `[]` | Glob patterns to skip, e.g. `["private/**"]` |
| `max_tool_calls` | `15` | Tool calls allowed per question |
| `tool_output_chars` | `12000` | Truncation limit for each tool result |
| `privacy_notice` | `true` | Warn when a cloud model will receive your data |
| `exiftool` | `exiftool` | Path to the ExifTool binary |

## Privacy

With an `ollama:` model, nothing leaves your computer. With a cloud model, your questions and the
tool results (names, dates, document passages) are sent to that provider; genea warns you once per
session.

## Model choice

The live evaluation (`uv run pytest -m live`, 15 questions) has not been run yet: no pass rate is
recorded. Run it against your model and compare with `GENEA_LIVE_MODEL=ollama:qwen3:8b`.

## Limitations (v1)

No OCR (scanned PDFs are listed as `needs_ocr` by `genea status`), no image-content analysis, and
no direct GEDCOM writing. Name matching does not handle Czech declension ("Annou Svobodovou"),
so full-text and semantic search do that work.

Known limitations: `ask` and `chat` are tested against PydanticAI's test models and a local fake
OpenAI-compatible HTTP server, not yet against a real Ollama model (see Model choice above).

## Development

```powershell
uv run pytest                 # unit and integration tests (no network)
uv run pytest -m live         # end-to-end questions against a real Ollama model
```

Copy real `.ged` files into `tests/private/` (git-ignored) to check they survive a parse/serialize
round trip byte for byte.
