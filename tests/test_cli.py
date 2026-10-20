from typer.testing import CliRunner
from telegram_migrate.cli import app
import pytest

runner = CliRunner()


@pytest.mark.parametrize("group,command", [
    ("auth", "login"), ("auth", "status"), ("auth", "remove-local"), ("access", "wizard"),
    ("chats", "list"), ("chats", "inspect"), ("mappings", "add"), ("mappings", "list"),
    ("members", "collect"), ("members", "list"), ("members", "export"), ("members", "import"),
    ("consent", "import"), ("consent", "revoke"), ("invites", "plan"), ("invites", "execute"),
    ("announcement", "draft"), ("migration", "plan"), ("migration", "execute"),
    ("migration", "resume"), ("migration", "status"), ("migration", "refresh-plan"),
    ("data", "delete"), ("data", "prune")])
def test_documented_commands_exist(group, command):
    result = runner.invoke(app, [group, command, "--help"])
    assert result.exit_code == 0, result.output + str(result.exception)


def test_offline_list_and_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_MIGRATE_HOME", str(tmp_path))
    from telegram_migrate.database import Store
    db = Store(tmp_path)
    db.execute("INSERT INTO mappings VALUES (1,-10011,-10022,'old','new')")
    db.close()
    assert runner.invoke(app, ["members", "list"]).exit_code == 0
    result = runner.invoke(app, ["announcement", "draft", "--mapping", "1"])
    assert result.exit_code == 0 and "Joining is optional" in result.output
