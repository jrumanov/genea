# Clickable citations with a local viewer

## Intent

The user works with genea through Claude Code (the genea MCP server). Claude cites everything as
`[docs/matrika.pdf p.3]`, `[tree.ged:L1042]`, `[recordings/x.txt @14:20]` or `[photos/x.jpg]`. Today
those are plain text; to check a source the user has to find the file by hand. They want to click a
citation and see the source at exactly the cited place.

Why a viewer and not "open in the default app": default apps cannot be told where to go. A link or
ShellExecute opens md, `.ged`, `.txt` at the top, ignores a line number, does not seek in audio, and
honours a PDF page only when the default PDF app is a browser. The user chose a small local viewer
page that scrolls to and highlights the passage over file-level opening.

## Goals

- Every citation Claude writes is a link; clicking it opens the viewer in the browser at the cited place.
- Viewer by citation kind:
  - text documents (md, extracted records, transcripts' text): full text, scrolled to the cited chunk, highlighted;
  - recordings: the transcript text with the cited moment highlighted. No audio player, no audio at all;
  - PDFs: the PDF in the browser's PDF viewer at the cited page; the passage is highlighted when the PDF has a text layer;
  - photos: the image with its indexed metadata (title, description, date, place, persons shown) beside it;
  - GEDCOM lines: the record around that line (the individual or family block), the cited line highlighted.
- Works with no extra step: the viewer starts together with the MCP server and needs no separate process.
- Read-only and local.

## Non-goals

- Opening files in the user's default apps (dropped in favour of the viewer).
- Audio playback.
- Links that survive after the Claude Code session ends or the MCP server restarts. The user accepts
  this for now; a separate always-on viewer service is a later iteration, so do not close the door on it
  (keep the viewer a self-contained module that the MCP server merely starts).
- Highlighting words in scanned PDFs without a text layer (needs OCR, separate roadmap item). Those open at the page.
- Editing anything. Writing back to the tree is a separate project.
- Use outside Claude Code (the built-in `genea chat` agent): not required; not forbidden.

## Anti-goals (would make it a failure even if it technically works)

- The viewer serves a file outside the KB root, or is reachable from another machine.
- A link opens a file type the user excluded from handling: travel card, resident permit, id card,
  driving licence and health insurance card images (see the excluded-document-tags rule). Those must not be served.
- Citations change shape so that Claude can no longer copy them from tool results exactly, which
  breaks the citation rule in the system prompt.
- Tool results get noticeably bigger and push evidence out of the MCP output budget (`mcp_output_chars`).

## Decisions made with the user

- Click opens a local viewer page (not the default app) for every kind, photos and PDFs included.
- Precision follows what is indexed: page, line, timestamp now; in-passage highlighting once offsets exist.
- The viewer lives only while Claude Code's genea MCP server runs.
- Markdown links to `http://127.0.0.1:PORT/...` are clickable in the user's Claude Code (tested 2026-10-26), so no fallback tool is needed.
- Both slices are in scope, as two PRs. Slice 2 changes the index schema; a schema bump and a full KB index rebuild are accepted.

## Decisions made by the designer (the user has not confirmed these)

- The MCP server starts an HTTP server on `127.0.0.1` in a background thread. Default port fixed and
  configurable in settings; if it is taken, fall back to a free port and use that in links.
- The visible citation text stays exactly as today. Only the link target is added: the system prompt
  tells Claude to write `[docs/matrika.pdf p.3](http://127.0.0.1:PORT/...)`.
- Two slices, two PRs. Slice 1: the viewer and links with page/line/timestamp precision; build the
  highlight code so it can accept offsets later. Slice 2: character offsets inside a chunk (schema bump,
  indexer, `SourceRef`) so a sentence can be highlighted.
- Port reaches Claude through the links in tool results (built from the running server's actual port).

## Constraints

- Python project in `src/genea`; citations are built by `SourceRef.cite()` in `src/genea/tools/models.py`
  from `file`, `line`, `page`, `chunk`, `start_s`. The index (`chunks` table) stores `page`, `chunk_no`,
  `text`, `start_s`, `end_s`, no offsets within the source file.
- The MCP server is `src/genea/mcp_server.py` (stdio, MCPServer). Instructions for Claude are in
  `MCP_INSTRUCTIONS` in `src/genea/agent/prompt.py`.
- KB root is a Windows path (`D:\Foto DB`); links must survive spaces, diacritics and backslashes in paths.
- The index may be incomplete or still building; the viewer must say so instead of failing mysteriously.
- Repo conventions: one commit per PR, squash merge, tests with the existing pytest setup, Windows
  parametrize-id length pitfall.

## Left to the builder

- Link format and URL scheme (path vs id based, escaping), as long as links are stable within a session.
- How to carry the link in tool results without bloating them (build it from the existing `SourceRef`
  fields at render time rather than storing it).
- HTTP library choice (stdlib vs something already a dependency).
- How to map a chunk to its place in the source text (chunk text may be normalised and not a byte-exact
  substring) and what to do when it cannot be found: open the document unhighlighted at the right page/start.
- Viewer look: plain, readable, works in light and dark; Slovak and Czech diacritics render correctly.
- How the viewer decides a file is excluded (use the existing tag handling).
- Tests, including path-traversal attempts and files outside the KB root.
