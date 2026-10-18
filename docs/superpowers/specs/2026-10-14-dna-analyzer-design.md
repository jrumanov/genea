# DNA analyzer: haplogroups and SNP lookup from a MyHeritage raw file

Date: 2026-10-14. Branch: `jrumanov/dna-analyzer`.

## Goal

Let genea analyse a locally stored MyHeritage raw DNA file with free, open-source tools and
expose the results to both the local-model agent and the MCP server (Claude), without ever
handing out the genotype in bulk.

Phase 1 delivers parsing and QC, mtDNA and Y haplogroups, single-SNP lookup, and the agent and
MCP tools. Phase 2 (a separate plan) adds ancestry composition against a public reference panel.

## Inputs and constraints

- The only input is `MyHeritage_raw_dna_data.csv` (`RSID, CHROMOSOME, POSITION, RESULT`, expected
  build GRCh37, roughly 600-700k SNPs). No match list, no segment data, no ethnicity export.
  Match and segment analysis against the tree is therefore **out of scope**.
- The donor is a male family member. The design is donor-agnostic: sex is detected from the data,
  Y steps run only when Y calls are present, mtDNA always runs, X is treated as diploid for females.
- Free and open source only. No API spend, CPU only, Windows 11, Python >= 3.12, Java 1.8 installed.
- **Multiple knowledge bases.** Nothing is hardcoded to a path. All per-KB data lives under
  `kb.meta_dir / "dna"`. `D:\Foto DB` stays only the default KB resolution that already exists.
- Privacy decision (user, 2026-10-14): both interfaces get the same tools with limits
  (per-call cap and per-session budget below). Over MCP, tool output reaches Claude and is stored
  in Claude Code transcripts; the limits reduce exposure but do not make it zero.

## Components

New package `src/genea/dna/`:

| Unit | Responsibility | Depends on |
|---|---|---|
| `parse.py` | Read the raw CSV (via `snps`) into a normalized table `(rsid, chrom, pos, genotype)`; detect build; reject non-MyHeritage or malformed files with the first bad line | `snps` |
| `qc.py` | Call rate, per-chromosome counts, sex inference (X heterozygosity, Y calls), count of mtDNA and Y markers | parse |
| `haplo.py` | Run `yhaplo` (Y, only when Y calls exist) and Haplogrep (mtDNA); normalize to one record: haplogroup, confidence or quality, markers used, tool and tree version | external tools |
| `store.py` | Per-person directory with `meta.json`, `haplogroups.json`, `genotypes.sqlite` (rsid-indexed); atomic writes (temp file then rename) | none |
| `tools.py` | Read-only tool functions shared by the agent and MCP | store |
| `limits.py` | Per-call cap and per-session budget for `snp_lookup` | none |

`KnowledgeBase` gains `dna_dir` (`meta_dir / "dna"`), added to `SUBDIRS`-style creation on demand.

**Storage layout**

```
<kb root>/.genea/dna/<person-slug>/meta.json          # gedcom id, build, sex, QC, import date
                                  /haplogroups.json   # results with markers, tool + tree versions
                                  /genotypes.sqlite   # rsid -> chrom, pos, genotype
~/.genea/dna-tools/                                    # global, shared by all KBs: Haplogrep jar,
                                                       # portable JRE (fallback), later the
                                                       # ancestry reference panel
```

The layout is per person from day one, so a relative's file can be added later (kinship and IBD
between two files is a future feature, not in this spec). `.genea/dna/` is gitignored by default,
is not part of the document index or embeddings, and is never reachable through
`search_documents` or `read_document` (`resolve_inside` already refuses `.genea`). The raw CSV
stays where the user keeps it; only `genotypes.sqlite` is copied into the KB.

## CLI (the only place computation happens)

- `genea dna setup`: download the Haplogrep jar into `~/.genea/dna-tools/`; check Java and `yhaplo`;
  download the portable JRE when no suitable system Java exists (see "Java runtime").
- `genea dna import <csv> --person <gedcom-id> [--name <slug>] [--replace]`: parse, QC, store.
- `genea dna analyze <person>`: run the haplogroup tools, write `haplogroups.json`.
- `genea dna status`: people, QC summary, which analyses exist.
- `genea doctor` reports missing Java, Haplogrep or `yhaplo`, and which Java is in use.

### Java runtime

Only the mtDNA step needs Java (Haplogrep); `yhaplo` is pure Python. A JRE is enough, no JDK.
Resolution order, decided by `haplo.py` and reported by `doctor`:

1. A system Java (on `PATH` or `JAVA_HOME`) whose version meets the minimum required by the chosen
   Haplogrep version (the version check is part of "Verify before implementing").
2. Otherwise a **portable JRE** (for example a Temurin JRE archive, about 50 MB) that
   `genea dna setup` downloads and unpacks into `~/.genea/dna-tools/jre/`. It is shared by all
   KBs, and genea invokes it by absolute path.

The system Java, `PATH` and `JAVA_HOME` are never modified. The portable download is skipped when
a suitable system Java exists, and a pinned download URL and checksum are verified before unpacking.
If neither works, `analyze` skips mtDNA with a clear message and the Y step still runs.

Optional dependencies go in a new `dna` extra (`snps`, `yhaplo`), like the existing `transcribe`
and `convert` extras.

## Tools (read-only, identical on the local agent and MCP)

- `dna_summary(person)`: QC, inferred sex, which analyses exist, linked tree person if the index
  is available.
- `haplogroups(person)`: results with markers used and an explicit resolution caveat.
- `snp_lookup(person, rsids)`: genotype per rsID. An unknown rsID answers "not on this chip",
  which is not the same as "no variant".

Outputs carry citations such as `[dna/<person>/haplogroups.json]`, so they can be used as sources
in `propose` (for example an `add_note` recording a haplogroup on a person). That remains a
human-reviewed proposal; the tree is never changed by DNA tools.

There are no import or analyze tools on MCP or the agent. A tool call can never start Java or
write genotype data. If nothing has been analysed, the tool says so and names the CLI command.

### Privacy limits

- `snp_lookup` rejects more than **20 rsIDs per call** (rejected, not truncated).
- A **per-session budget** counts rsIDs served per server or agent session and refuses past a
  limit (default **200**, configurable in the KB `config.toml`). The refusal says it is a
  deliberate privacy limit and must not be worked around. The cap alone is only friction, because
  a model could page through the genotype 20 at a time.
- No tool returns whole chromosomes, the file, or the path to the raw CSV.

### Prompt addendum (shared by `MCP_INSTRUCTIONS` and the agent prompt)

Haplogroups describe one direct line (Y paternal, mtDNA maternal), not the whole person; chip
calls have limited resolution; a haplogroup is supporting evidence, never proof of a specific
tree link; do not reconstruct the genotype from many small lookups.

## MCP integration

- Process model unchanged: Claude Code starts `genea mcp --kb <root>` over stdio, bound to one KB.
  DNA tools read only that KB's `.genea/dna/`.
- Registered in `build_server()` in `src/genea/mcp_server.py` with `@server.tool(structured_output=False)`
  and `async def` wrappers, like the existing tools. The local agent registers the same functions
  in its tool list (exact spot in `src/genea/agent/` to be confirmed when planning).
- DNA tools bypass `IndexGate`: they need no document index, so they work before `genea index`
  has run and while it is running. Only the optional name and life dates of the linked tree person
  use the index, and degrade gracefully when it is missing. A small `run_kb` helper gives them the
  same error handling as `run`, without the gate.
- The tool functions, docstrings, cap and refusal text are written once and shared.

## Error handling

- Not a MyHeritage raw file, or an unknown build: reject with the first bad line. A build other
  than GRCh37 stops the import with a clear message; no silent liftover.
- Call rate below 95%: warning, not an error.
- Person already imported: requires `--replace`.
- A male label but no Y calls: Y is skipped and the reason recorded; no invented result.
- Java too old and no portable JRE, or Haplogrep or `yhaplo` missing: `analyze` stops with the exact
  fix (typically `genea dna setup`). A failed JRE download or checksum mismatch leaves no partial
  unpack behind.
- A failed tool never leaves a half-written `haplogroups.json` (atomic writes).
- Before analysis: "not analyzed yet, run `genea dna analyze <person>`".

## Testing

- **Unit (CI):** parsing on a small synthetic MyHeritage-format CSV in the test fixtures (invented
  data, never the real file): header and quoting quirks, no-calls (`--`), X/Y/MT rows, sex
  inference for synthetic male and female donors. Store round-trips, cap and budget logic.
- **Tool layer:** call the shared functions and check output text, citations and refusals. One MCP
  test lists the tools and calls each through the server, like the existing MCP tests.
- **Multi-KB:** two KBs in a temp dir; no data leaks between them and nothing depends on a
  hardcoded path.
- **Live (`-m live`):** real Haplogrep and `yhaplo` runs on a public sample genotype, skipped when
  Java or the tools are absent. The real file is used only in a manual check.

## Phasing

1. **Phase 1 (this spec's plan):** `kb.dna_dir`, the `genea dna` commands, parse, QC, sex
   inference, mtDNA and Y haplogroups, the three tools on both interfaces with cap, budget and prompt
   addendum, `doctor` checks.
2. **Phase 2 (separate plan, high level only):** ancestry composition via PCA projection onto a
   downloaded public panel (1000 Genomes / HGDP) and an `ancestry` tool, as a second opinion on
   MyHeritage. Public panels are thin for Central Europe, so resolution will be rough. The panel
   and method are chosen after a quick experiment on the real data.

Deferred and opt-in: health and trait annotation (ClinVar / SNPedia). Sensitive; only on request.

## Verify before implementing

- Which Haplogrep version runs on the installed Java 1.8, and so whether the portable JRE fallback
  is needed on this machine; pick the JRE distribution, version, URL and checksum for it.
- How many mtDNA and Y markers a MyHeritage chip actually covers; this decides the haplogroup
  resolution and what the caveat text must say.
- Whether `yhaplo` accepts the MyHeritage format directly or needs conversion to its supported
  input.
- The exact registration point for the local agent's tool list.

## Out of scope

Match and segment analysis, kinship or IBD between files, ethnicity import, health and trait
annotation, any write to the GEDCOM, and any tool that computes or imports over MCP.
