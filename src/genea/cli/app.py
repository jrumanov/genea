"""Typer application objects. Command modules register themselves on these."""

import typer

from genea.cli.common import use_utf8_stdout

app = typer.Typer(no_args_is_help=True, help="Genealogy knowledge-base agent.")
changes_app = typer.Typer(no_args_is_help=True, help="Review and apply changes proposed by the agent.")
app.add_typer(changes_app, name="changes")


@app.callback()
def main(
    ctx: typer.Context,
    kb: str | None = typer.Option(
        None, "--kb", help="Knowledge-base folder, or a name from [kbs] in ~/.genea/config.toml."
    ),
) -> None:
    use_utf8_stdout()
    ctx.obj = {"kb": kb}
