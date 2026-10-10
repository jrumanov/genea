"""`genea transcribe`, `genea transcribe-model convert` and `genea transcripts check / repair`."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.markup import escape

from genea.cli import common
from genea.cli.app import app, transcribe_model_app, transcripts_app
from genea.config import KBNotFound, Settings, load_settings, resolve_kb_root
from genea.ingest.transcript import Segment, format_time
from genea.transcribe.audio import SAMPLE_RATE, AudioUnavailable, audio_duration, load_span
from genea.transcribe.check import check_file, describe_span, transcripts
from genea.transcribe.convert import ConvertError, convert_model
from genea.transcribe.doublepass import NoSpeech, double_pass
from genea.transcribe.engine import (
    HF_MODELS,
    ModelUnavailable,
    Transcriber,
    WhisperCpp,
    language_label,
    models_dir,
    resolve_language,
    resolve_model,
)
from genea.transcribe.progress import Progress
from genea.transcribe.repair import RepairError, SpliceError, format_segment, repair_transcript


def say(message: str) -> None:
    typer.echo(message)


def make_transcriber(settings: Settings, model: str | None) -> Transcriber:
    """The real whisper.cpp transcriber; tests replace this function."""
    return WhisperCpp(resolve_model(model or settings.transcribe_model, say), say=say)


def ensure_models(settings: Settings, names: list[str | None]) -> None:
    """Finds, downloads or converts each model now, so a wrong name fails before any audio is decoded."""
    for name in names:
        resolve_model(name or settings.transcribe_model, say)


def _fail(message: str) -> typer.Exit:
    common.console.print(f"[red]{escape(message)}[/red]", soft_wrap=True)  # no hard breaks inside a command
    return typer.Exit(1)


def _settings(ctx: typer.Context) -> Settings:
    try:
        root = resolve_kb_root(ctx.obj["kb"])
    except KBNotFound:
        root = None
    return load_settings(root)


MODEL_OPTION = typer.Option(None, "--model", help="Model name (downloaded or, for sk-small, sk-medium and "
                                                   "sk-large-v3-turbo, converted on first use) or path of a ggml "
                                                   ".bin; default: transcribe_model.")
LANGUAGE_OPTION = typer.Option(None, "--language", help="Language code such as sk; default: transcribe_language, "
                                                         "and when that is empty, auto-detect.")
STRUCTURE_OPTION = typer.Option(None, "--structure-model",
                                help="Double pass: a small model (such as base) finds the segment boundaries, "
                                     "then --model writes the text of each. Default: transcribe_structure_model; "
                                     "empty means a single pass.")


@app.command()
def transcribe(
    ctx: typer.Context,
    audio: Path = typer.Argument(..., exists=True, dir_okay=False, help="The recording."),
    model: str | None = MODEL_OPTION,
    structure_model: str | None = STRUCTURE_OPTION,
    language: str | None = LANGUAGE_OPTION,
    start: float = typer.Option(0.0, "--start", min=0.0, help="Transcribe only from this second."),
    out: Path | None = typer.Option(None, "--out", help="Output file (default: next to the audio, .txt)."),
) -> None:
    """Transcribe a recording into a Whisper-format .txt, with live progress."""
    settings = _settings(ctx)
    target = out or audio.with_suffix(".txt")
    if target.exists():
        raise _fail(f"{target} already exists; pass --out to write elsewhere.")
    if not target.parent.is_dir():
        raise _fail(f"The folder {target.parent} does not exist; create it or pass another --out.")
    lang = resolve_language(language, settings.transcribe_language)
    say(f"Language: {language_label(lang)}")
    structure = settings.transcribe_structure_model if structure_model is None else structure_model
    transcriber = None
    try:
        if structure:
            ensure_models(settings, [structure, model])  # both names are checked before pass 1 starts
            say(f"Double pass: boundaries from {structure}, text from {model or settings.transcribe_model}")
        else:
            transcriber = make_transcriber(settings, model)
        samples = load_span(audio, start, None)
    except (ModelUnavailable, AudioUnavailable) as exc:
        raise _fail(str(exc)) from exc
    total = len(samples) / SAMPLE_RATE
    progress = Progress(total, start)
    prefix = "[2/2 text] " if structure else ""
    # Written aside and renamed at the end, so a failed run never leaves a partial transcript under the
    # real name (it would be indexed, and the next run would refuse to overwrite it).
    part = target.with_name(target.name + ".part")
    text_started = not structure  # False while pass 1 runs: the .part file is still empty then
    try:
        with part.open("w", encoding="utf-8", newline="") as file:
            def on_segment(s: Segment) -> None:
                file.write(format_segment(Segment(s.start + start, s.end + start, s.text)) + "\r\n")
                file.flush()
                say(prefix + progress.line(s.end, s.text))

            if structure:
                structure_progress = Progress(total, start)

                def on_structure(s: Segment) -> None:
                    say("[1/2 structure] " + structure_progress.line(s.end, s.text))

                def on_text_start() -> None:
                    nonlocal progress, text_started
                    text_started = True
                    say(f"Structure pass finished in {structure_progress.elapsed() / 60:.1f}m")
                    progress = Progress(total, start)  # the estimate of pass 2 must not include pass 1

                double_pass(samples, lang, lambda: make_transcriber(settings, structure),
                            lambda: make_transcriber(settings, model), on_segment, say=say,
                            on_structure=on_structure, on_text_start=on_text_start)
            else:
                transcriber(samples, lang, on_segment)
    except KeyboardInterrupt:
        if text_started:
            say(f"Interrupted; {target.name} was not written. The part transcribed so far is in {part} "
                "(a new run starts over and replaces it).")
        else:
            say(f"Interrupted during the structure pass; {target.name} was not written.")
        raise typer.Exit(130) from None
    except (ModelUnavailable, NoSpeech) as exc:
        part.unlink(missing_ok=True)
        raise _fail(str(exc)) from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    try:
        os.replace(part, target)
    except OSError as exc:
        raise _fail(f"Could not rename {part} to {target.name}: {exc}") from exc
    say(progress.summary(target))


@transcribe_model_app.command("convert")
def convert(
    source: str = typer.Argument(..., help="Hugging Face model (owner/name, or sk-small / sk-medium / sk-large-v3-turbo) "
                                           "or a folder holding a downloaded safetensors checkpoint."),
    name: str | None = typer.Option(None, "--name", help="Name of the converted model (default: the short name "
                                                         "or the last part of SOURCE)."),
    force: bool = typer.Option(False, "--force", help="Replace an existing converted model."),
) -> None:
    """Convert a Hugging Face Whisper checkpoint to the ggml file whisper.cpp loads; then use its name as --model."""
    repo = HF_MODELS.get(source, source)
    name = name or (source if source in HF_MODELS else Path(repo.rstrip("/\\")).name)
    try:
        target = models_dir() / f"ggml-{name}.bin"
        if target.exists() and not force:
            raise _fail(f"{target} already exists; pass --force to replace it.")
        convert_model(repo, target, say)
    except (ModelUnavailable, ConvertError) as exc:
        raise _fail(str(exc)) from exc
    say(f"Done: {target}")
    say(f"Use it with `--model {name}` or set transcribe_model = \"{name}\".")


@transcripts_app.command()
def check(
    ctx: typer.Context,
    path: Path | None = typer.Argument(None, exists=True, dir_okay=False,
                                       help="One transcript; default: every transcript in the knowledge base."),
) -> None:
    """List transcripts where Whisper looped or stopped early. Exit code 1 when any are found,
    2 when the file given is not a Whisper transcript."""
    kb = None if path else common.open_kb(ctx)
    items = [(path, None)] if path else transcripts(kb)  # the KB scan already read each transcript once
    found = 0
    unchecked = False
    for candidate, segments in items:
        result = check_file(candidate, duration_fn=audio_duration, segments=segments)
        if result is None:
            if path:
                common.console.print(f"[red]{escape(str(path))} is not a Whisper transcript "
                                     f"(no start/end timestamp lines).[/red]", soft_wrap=True)
                raise typer.Exit(2)
            continue
        unchecked = unchecked or result.audio_seconds is None
        if not result.spans:
            continue
        found += 1
        say(kb.rel(candidate) if kb else str(candidate))
        for span in result.spans:
            say(f"  {describe_span(span)}")
        total = f" of {format_time(result.audio_seconds)}" if result.audio_seconds else ""
        say(f"  real text: 0:00–{format_time(result.speech_end)}{total}")
    if not found:
        say("No lost spans found.")
    if unchecked:
        say("Some transcripts have no readable audio next to them (or the transcribe extra is missing), "
            "so early ends could not be checked for them.")
    raise typer.Exit(1 if found else 0)


@transcripts_app.command()
def repair(
    ctx: typer.Context,
    transcript: Path = typer.Argument(..., exists=True, dir_okay=False, help="The transcript to repair."),
    from_s: float | None = typer.Option(None, "--from", min=0.0, help="Re-transcribe from this second instead."),
    model: str | None = MODEL_OPTION,
    language: str | None = LANGUAGE_OPTION,
    no_splice: bool = typer.Option(False, "--no-splice", help="Only write the new text to .genea/work/."),
) -> None:
    """Re-transcribe the lost part of a transcript from its audio and splice it in."""
    kb = common.open_kb(ctx)
    lang = resolve_language(language, kb.settings.transcribe_language)
    say(f"Language: {language_label(lang)}")
    if not lang:
        say("The language is auto-detected; for a repair a fixed language is safer: "
            "set transcribe_language in the KB config or pass --language.")
    holder: list[Transcriber] = []

    def transcriber(samples, language_code, on_segment) -> None:
        if not holder:  # the model is loaded only when there is something to transcribe
            holder.append(make_transcriber(kb.settings, model))
        holder[0](samples, language_code, on_segment)

    try:
        report = repair_transcript(transcript, kb.meta_dir / "work", transcriber=transcriber, language=lang,
                                   say=say, from_s=from_s, splice_result=not no_splice,
                                   duration_fn=audio_duration, load_fn=load_span)
    except (ModelUnavailable, AudioUnavailable, RepairError, SpliceError) as exc:
        raise _fail(str(exc)) from exc
    except KeyboardInterrupt:
        say("Interrupted; the transcript is untouched and the part transcribed so far stays in .genea/work/")
        raise typer.Exit(130) from None
    if report.nothing_to_do:
        say("Nothing to repair: no lost spans found.")
        return
    for span, count in report.replaced:
        end = "end" if span.end is None else format_time(span.end)
        say(f"Replaced {format_time(span.start)}–{end} with {count} new segments")
    for _span, start, end in report.unreadable:
        say(f"still unreadable at {format_time(start)}–{format_time(end)}: left as it was")
    if report.spliced:
        say("Run `genea index` to re-index this transcript (its summary is regenerated).")
    elif no_splice:
        say(f"Not spliced (--no-splice). The new text is in {report.work_file}")
    if report.unreadable:
        raise typer.Exit(1)
