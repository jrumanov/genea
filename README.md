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

## Use with Claude (MCP)

`genea mcp` serves the knowledge base to Claude Code or Claude Desktop as an MCP server (search, people,
relatives, timelines, documents (including `documents_about`, every document that mentions a person), photos, conflict checks). It is read-only except for analyses, which it
writes only under `.genea/analyses/`: the tools `analyze`, `read_analysis`, `propose`, `withdraw`,
`add_analysis_note` and `build_iteration` (see [Analyses](#analyses-proposing-corrections-to-the-tree)).
The tree itself is never changed, and dismissing a finding is CLI only.

```bash
uv sync --extra mcp
genea --kb <kb> index        # Claude can connect before this finishes, see below
```

Claude Code:

```bash
claude mcp add genea -- uv run --directory <path to this repo> --extra mcp genea --kb <kb> mcp
```

Claude Desktop (`claude_desktop_config.json`; use absolute paths, and double backslashes on Windows):

```json
{
  "mcpServers": {
    "genea": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\genealogy-rag", "--extra", "mcp", "genea", "--kb", "C:\\path\\to\\kb", "mcp"]
    }
  }
}
```

Ollama is optional here: without it, document search falls back to keywords. Tool results, which contain
your family-tree data, are sent to the Claude client you connect (hide the startup note with
`privacy_notice = false`).

Claude can connect before or during indexing. Until the knowledge base is indexed, the tools reply that it
is not indexed or is being built (with progress and an ETA). While `genea index` runs, or after a run that
did not finish, results carry a note that they may be incomplete. Ask Claude for the `index_status`, or
run `genea status`, to see the progress. On Windows, `genea index --rebuild` cannot delete an index that
Claude has open: close the Claude client first.

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
| `genea init` / `index [--rebuild] [--no-summaries]` / `status` | Set up, index, and inspect a knowledge base |
| `genea summaries pending` / `import [--model M]` | Hand transcript summaries to an outside summarizer (Claude Code) and store its answers |
| `genea ask "..."` / `chat` | Talk to the agent (`--show-tools` shows its tool calls) |
| `genea person <name or id>` | Look a person up without the LLM |
| `genea conflicts [id]` | Rule-based consistency checks without the LLM |
| `genea analyze person\|family\|branch <id> [--direction D] [--depth N]` | Check part of the tree for conflicts and gaps; writes a report to `.genea/analyses/` |
| `genea analysis list / show / propose / withdraw / dismiss / build` | Review findings, record proposals and build a corrected copy of the tree |
| `genea dna setup / import / analyze / status` | Call mtDNA and Y haplogroups from a MyHeritage raw DNA file (see "DNA") |
| `genea mcp` | Serve the knowledge base to Claude over MCP (read-only, except analyses; see "Use with Claude") |
| `genea transcribe <audio> [--model M] [--language L] [--start S] [--out F]` | Transcribe a recording with whisper.cpp into a Whisper-format `.txt`, with live progress |
| `genea transcribe-model convert <hf-repo\|folder\|sk-small\|sk-medium\|sk-large-v3-turbo> [--name N] [--force]` | Convert a Hugging Face Whisper checkpoint to a whisper.cpp model, e.g. the Slovak fine-tunes |
| `genea transcripts check [path]` | List transcripts where Whisper looped or stopped early (exit code 1 if any) |
| `genea transcripts repair <transcript> [--from S] [--model M] [--language L] [--no-splice]` | Re-transcribe the lost part from the audio and splice it in |
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

## Analyses: proposing corrections to the tree

`genea analyze` (or the MCP tool `analyze`) checks a person, a family or a branch for conflicts (events after
death, a birth after the mother's death, siblings born too close together…) and gaps (no birth, no death for
someone born over 110 years ago, no parents, events without a source). Detectors only find problems; they
never guess a value.

Corrections are **proposals** with citations and a reason, recorded by Claude (`propose`) or by you
(`genea analysis propose`). `genea analysis build <name>` (MCP `build_iteration`) then writes
`.genea/analyses/<name>/vN.ged`: a full copy of the real tree with every active proposal applied, each marked
by a `NOTE` starting `GENEA vN`. Conflicts (`error` and `needs_review` findings) nobody has decided on get a
`GENEA CHECK` note; gaps (`info` findings) are listed in the report only. Next to it are `vN.diff` and `vN.md`
(the report with each change's diff).

- The real `.ged` is never changed. Each iteration is rebuilt from it, so v2 does not contain v1's notes.
- If the tree changed since a proposal was made (a new Gramps export), an `update_event` proposal is re-found by
  its event (type, value, date and place); if that is not possible, or the event is in the record more than
  once, it is marked stale and left out, never applied to the wrong line. Other operations are located by the
  person's or family's xref (stale if that record is gone).
- `genea analysis dismiss <name> <finding>` marks a finding as not a problem (only you can do this).
- Only UTF-8 and ANSI (CP1250) trees can be written; export from Gramps as UTF-8.
- An iteration cannot be imported into Gramps (Gramps would duplicate everyone). Review the diff and enter the
  changes in Gramps; `GENEA` notes are never indexed as evidence.

## DNA (MyHeritage raw data)

genea can read a MyHeritage raw DNA download (`MyHeritage_raw_dna_data.csv`, build 37) for one person of the tree
and call the mtDNA (maternal line) and Y (paternal line) haplogroups with free tools.

    uv sync --extra dna
    genea dna setup                                   # Haplogrep 3 (+ a portable Java 21 if yours is older than 11)
    genea dna import MyHeritage_raw_dna_data.csv --person "tree.ged#@I12@"
    genea dna analyze tree.ged-I12
    genea dna status

- Data stays in `<knowledge base>/.genea/dna/<person>/`; it is never indexed or searchable. The raw CSV is not copied.
- The tools go to `~/.genea/dna-tools/` and are shared by all knowledge bases; your system Java is not changed.
- The agent and the MCP server get read-only tools: `dna_summary`, `haplogroups` and `snp_lookup`. `snp_lookup`
  answers at most 20 rsIDs per call and 200 per session (`dna_session_rsids` in `config.toml`). Over MCP, results
  are sent to Claude.
- Licenses: Haplogrep 3 is MIT; yhaplo (23andMe) is free for non-commercial use by individuals.

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
| `log_prompts` | `true` | Append every `ask`/`chat` question and answer to `.genea/prompts.md` (fill in `verdict`/`note` by hand to mark good and bad answers) |
| `exiftool` | `exiftool` | Path to the ExifTool binary |
| `summary_model` | `""` | Ollama model for transcript summaries (empty: the chat model, if it is an `ollama:` one) |
| `transcribe_model` | `medium` | whisper.cpp model for `genea transcribe` / `transcripts repair` (a name, `sk-small`, `sk-medium`, `sk-large-v3-turbo`, or the path of a ggml `.bin`) |
| `transcribe_language` | `""` | Language code such as `sk`; empty means auto-detect |
| `transcribe_structure_model` | `""` | Small model for the double pass of `genea transcribe` (for example `base`); empty means a single pass |

## Transcripts

A `.txt` file made of Whisper segments (`start<TAB>end<TAB> text`) is indexed as a recording: chunks keep
their time in the recording, and answers cite a moment, like `[podcast_family/2022_05_10-x.txt @14:20]`.
`read_document` with `start_s` reads a transcript from that second onward.

`genea index` also asks `summary_model` (through Ollama) for a summary of each recording and of each
section of about 12,000 characters. Summaries are stored in `.genea/summaries/`, one file per transcript,
so `index --rebuild` and model changes do not run the LLM again; a summary is made again only when the
transcript changes or the cache file is deleted and the transcript is re-indexed. Summaries help to find
the right recording and moment; the agent is told to read the transcript and cite that, never the summary.
The first run over many long recordings takes a while; `genea index --no-summaries` skips this
step, and a later `genea index` fills the summaries in.
Ollama must be running for indexing (embeddings use it too). If the summary model fails or times out,
the transcript is still indexed and searchable, and the summary is retried on the next run (`genea status`
lists these as `summary pending`).

Summaries can also be written outside genea, for example by Claude Code when the local model is too slow:
`genea summaries pending` writes each transcript without a summary to `.genea/summary-work/` (the text in
sections, plus `INSTRUCTIONS.md` with the same prompts), the summarizer writes a `<digest>.result.json`
beside each one, and `genea summaries import` checks and caches them. The next `genea index` adds them,
even without a `summary_model`. In this repo, ask Claude Code to "summarize pending transcripts" (the
`summarize-transcripts` skill). This sends the transcripts to Anthropic.

Upgrading: the index format changed (schema 2). Run `genea index --rebuild --no-summaries` first to make
everything searchable quickly, then `genea index` to add the summaries (cached ones are reused).

## Transcribing and repairing recordings

Whisper sometimes loops on one line until the end of a recording, or stops early. The index then holds an
incomplete recording without saying so. `genea transcripts check` finds these (a run of at least three identical
segments lasting a minute or more, or a transcript that ends a minute or more before its audio) and
`genea doctor` mentions them. `genea transcripts repair <transcript>` cuts the audio from the segment before the
lost part, transcribes it again, and splices the result in. The first original is kept as `<name>.txt.orig`,
other lines are not touched, and a result that loops again is not spliced. Run `genea index` afterwards:
only that transcript is indexed again and its summary regenerated.

`genea transcribe <audio>` makes a transcript from scratch. Both commands print a line per decoded segment:
`[ 98.1%] 39:55  elapsed 11.0m  eta 0.2m  To sú kokosové.`

They need the extra `transcribe` (`uv sync --extra transcribe`; `check` works without it but cannot read audio
lengths). The model is `transcribe_model` (default `medium`; a name such as `large-v3` is downloaded on first
use, or give the path of a ggml `.bin`, for example the models of Audacity's OpenVINO plugin). `--language`
or `transcribe_language` fixes the language (`sk`); when neither is set it is auto-detected, which can go wrong
on a short span, so set it for a repair. The audio must sit next to the transcript with the same name
(`.m4a`, `.mp3`, `.wav`, `.mp4`, `.ogg` or `.flac`).

**Slovak fine-tunes.** `sk-small` ([NaiveNeuron/whisper-small-sk](https://huggingface.co/NaiveNeuron/whisper-small-sk)),
`sk-medium` ([kinit/whisper-medium-sk](https://slaih.sk/en/catalog/whisper-medium-sk))
and `sk-large-v3-turbo` ([NaiveNeuron/whisper-large-v3-turbo-sk](https://huggingface.co/NaiveNeuron/whisper-large-v3-turbo-sk))
are only published as Hugging Face checkpoints, which whisper.cpp cannot load. With the extra `convert`
(`uv sync --extra transcribe --extra convert`) they are downloaded and converted to ggml (float16, 0.5 GB for `sk-small`, 1.5 GB for the others)
on first use, so `--model sk-medium` or `transcribe_model = "sk-medium"` is all it takes; `genea transcribe-model
convert` does the same ahead of time, and also converts any other Whisper repository or downloaded folder
(`genea transcribe-model convert owner/name`, then `--model name`). The download is cached by Hugging Face, so
delete `~/.cache/huggingface` afterwards if you need the 1.5-3 GB. These models tend to write one long segment per
30 seconds instead of short timestamped ones, so the `@time` citations of such a transcript are coarser.

**Double pass.** The Slovak fine-tunes write the better words, but the generic models cut segments better, at
the places where the speaker or the sentence changes. `genea transcribe rec.m4a --structure-model base --model
sk-large-v3-turbo --language sk` runs the small model first to find the segment boundaries, then the fine-tune on
the audio of each segment, and writes the first model's times with the second model's text (or set
`transcribe_structure_model` and `transcribe_model`; `--structure-model ""` switches it off again). A segment
for which the fine-tune hears nothing or loops is left out, and a segment whose text is very different in
length from the first pass is kept but listed as `check:` in the output. It is slower than a single pass; how much
slower has not been measured yet. Speaker labels are not produced: Whisper does not tell speakers apart.

## Privacy

With an `ollama:` model, nothing leaves your computer. With a cloud model, your questions and the
tool results (names, dates, document passages) are sent to that provider; genea warns you once per
session.

## Model choice

The live evaluation (`uv run pytest -m live`, 15 questions) passed 14 of 15 with `qwen3:8b` on a
CPU-only machine (about 4 minutes per question). The miss was a Czech declension case: for "Kdo byli
sourozenci Karla Nováka narozeného 1871?" the model searched for "Karl Novák" instead of "Karel
Novák" and found nothing. Compare other models with `GENEA_LIVE_MODEL=ollama:<name>`.

## Limitations (v1)

No OCR (scanned PDFs are listed as `needs_ocr` by `genea status`), no image-content analysis, and
no direct GEDCOM writing. Name matching does not handle Czech declension ("Annou Svobodovou"),
so full-text and semantic search do that work.

The model may not normalize inflected Czech names to the nominative before searching (see Model
choice above).

## Development

```powershell
uv run pytest                 # unit and integration tests (no network)
uv run pytest -m live         # end-to-end questions against a real Ollama model
```

Copy real `.ged` files into `tests/private/` (git-ignored) to check they survive a parse/serialize
round trip byte for byte.
