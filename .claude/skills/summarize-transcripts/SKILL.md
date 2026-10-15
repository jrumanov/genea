---
name: summarize-transcripts
description: Use when the user asks to summarize pending transcripts / recordings of a genea knowledge base with Claude instead of the local Ollama model.
---

# Summarize transcripts with Claude Code

The local model is slow on this machine (CPU only), so transcript summaries can be written by Claude
Code instead, on the user's subscription. genea only exports the work and imports the answers; the
MCP server stays read-only.

The knowledge base is passed as `--kb <folder or name>`. The user's is `D:\Foto DB` (its index is
`D:\Foto DB\.genea`); never pass a subfolder such as `rodinna historia`: genea would create a second
`.genea/` there. Run genea from this repo with `uv run genea`.

1. **Export.** `uv run genea --kb "<kb>" summaries pending`. It lists each transcript without a summary
   and writes to `<kb>/.genea/summary-work/`: `INSTRUCTIONS.md`, and per transcript
   `<digest>.sections.md` (the text, in numbered sections) and `<digest>.request.json` (section times;
   do not edit). Nothing listed: stop, there is nothing to do.

2. **Summarize, one Haiku subagent per transcript.** Use the Agent tool with `model: "haiku"`, several
   in parallel (about 5 at a time). Do not read the transcripts yourself: they are long and would fill
   this conversation. Prompt for each:

   > Read `<work>/INSTRUCTIONS.md`, then `<work>/<digest>.sections.md` (read all of it, in parts if it
   > is long). Write `<work>/<digest>.result.json` exactly as INSTRUCTIONS.md describes: one entry per
   > section, in order, in the language of the transcript, using only what is said. Reply only "done"
   > or what went wrong; do not repeat the summaries.

3. **Import.** `uv run genea --kb "<kb>" summaries import --model claude-haiku-4-5`. It stores the valid
   results in the summary cache and deletes their work files. A failed one is listed with the reason
   and kept: rerun step 2 for just that transcript, then import again.

4. **Do not index.** Never run `genea index` after importing, not even to make the summaries searchable:
   the user runs the indexing manually. Tell the user how many were imported and anything that failed,
   and that the index still needs to be rebuilt.

The transcripts are private family recordings: this sends them to Anthropic. The user chose that
(2026-10-09); do not use this workflow on another knowledge base without asking.
