import pytest

from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.kb import KnowledgeBase
from genea.tools.base import open_tools
from genea.tools.curate import propose_change

runner = CliRunner()


def _setup(sample_root, monkeypatch):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n', encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    assert runner.invoke(app, ["--kb", str(sample_root), "index"]).exit_code == 0
    tools = open_tools(KnowledgeBase.open(str(sample_root)), FakeEmbedder())
    image = propose_change(tools, "image_xmp", "photos/svatba_1932.jpg", "set_fields",
                           {"XMP-dc:Title": "Svatba 1932"}, "better title")
    ged = propose_change(tools, "gedcom", "tree.ged", "add_event",
                         {"person_id": "tree.ged#@I9@", "type": "BAPM", "date": "1875"}, "matrika says so")
    tools.conn.close()
    return image.id, ged.id


def _run(sample_root, *args):
    return runner.invoke(app, ["--kb", str(sample_root), "changes", *args])


def test_changes_workflow(sample_root, monkeypatch):
    image_id, ged_id = _setup(sample_root, monkeypatch)
    listing = _run(sample_root, "list")
    assert listing.exit_code == 0 and image_id in listing.output and ged_id in listing.output

    shown = _run(sample_root, "show", ged_id)
    assert shown.exit_code == 0 and "BAPM" in shown.output and "matrika says so" in shown.output

    exported = _run(sample_root, "export")
    assert exported.exit_code == 0 and "checklist" in exported.output

    refused = _run(sample_root, "apply", ged_id)
    assert refused.exit_code == 1 and "export" in refused.output
    assert _run(sample_root, "apply", ged_id, "--manual").exit_code == 0

    assert _run(sample_root, "reject", image_id, "--reason", "not needed").exit_code == 0
    everything = _run(sample_root, "list", "--status", "all")
    assert "applied" in everything.output and "rejected" in everything.output

    assert _run(sample_root, "undo", image_id).exit_code == 1  # rejected, not applied
    assert _run(sample_root, "show", "chg-nope").exit_code == 1


@pytest.mark.exiftool
def test_apply_and_undo_report_failed_reindex(sample_root, monkeypatch):
    from genea.changes.store import ChangeStore

    image_id, _ = _setup(sample_root, monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("genea.changes.apply.reindex_file", broken)
    store = ChangeStore(KnowledgeBase.open(str(sample_root)))
    applied = _run(sample_root, "apply", image_id)
    assert applied.exit_code == 1
    assert applied.exception is None or isinstance(applied.exception, SystemExit), applied.exception
    assert "Applied" in applied.output and "re-index failed" in applied.output and "disk full" in applied.output
    assert "genea index" in applied.output
    assert store.get(image_id).status == "applied"

    undone = _run(sample_root, "undo", image_id)
    assert undone.exit_code == 1
    assert undone.exception is None or isinstance(undone.exception, SystemExit), undone.exception
    assert "Undone" in undone.output and "re-index failed" in undone.output
    assert store.get(image_id).status == "rejected"
