"""Typer application objects. Command modules register themselves on these."""

import typer

from genea.cli.common import use_utf8_stdout

app = typer.Typer(no_args_is_help=True, help="Genealogy knowledge-base agent.")
changes_app = typer.Typer(no_args_is_help=True, help="Review and apply changes proposed by the agent.")
app.add_typer(changes_app, name="changes")
summaries_app = typer.Typer(no_args_is_help=True,
                            help="Have transcripts summarized outside genea, for example by Claude Code.")
app.add_typer(summaries_app, name="summaries")
transcripts_app = typer.Typer(no_args_is_help=True,
                              help="Find and repair recordings whose transcript lost part of the speech.")
app.add_typer(transcripts_app, name="transcripts")
transcribe_model_app = typer.Typer(no_args_is_help=True, help="Prepare Whisper models for `genea transcribe`.")
app.add_typer(transcribe_model_app, name="transcribe-model")
analysis_app = typer.Typer(no_args_is_help=True,
                           help="Review analyses: findings, proposals and iterations of the tree.")
app.add_typer(analysis_app, name="analysis")


@app.callback()
def main(
    ctx: typer.Context,
    kb: str | None = typer.Option(
        None, "--kb", help="Knowledge-base folder, or a name from [kbs] in ~/.genea/config.toml."
    ),
) -> None:
    use_utf8_stdout()
    ctx.obj = {"kb": kb}
