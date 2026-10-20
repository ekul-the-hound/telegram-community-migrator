import asyncio
import csv
import json
import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from telegram_migrate.database import Store
from telegram_migrate.models import Chat, Member, Problem, now
from telegram_migrate.member_collection import collect
from telegram_migrate.planning import make_plan, fingerprint, refresh_plan
from telegram_migrate.migration import execute
from telegram_migrate.exports import import_members, export_members, import_consent, list_members
from telegram_migrate.security import Redactor, secret, worker_lock


def arun(awaitable):
    return asyncio.run(awaitable)


class Fake:
    def __init__(self):
        self.account_id = 202
        self.source = Chat(-10011, "Old broadcast", "broadcast", 202, True, False, False, False, False, True, 100, 900)
        self.dest = Chat(-10022, "Destination", "supergroup", 202, True, True, True, True, True)
        self.writes = []
        self.visible = [Member(9007199254740993, "fictional", "Fictional Person")]
        self.page_error = None
        self.add_error = None
        self.present = "absent"
        self.resolvable = True
        self.result = "added"
        self.after_add = None

    async def identity(self):
        return self.account_id

    async def inspect(self, identifier):
        return self.source if identifier == self.source.id else replace(self.dest, account_id=self.account_id)

    async def pages(self, chat):
        assert chat.id == self.source.id  # Never fetch the linked discussion chat.
        yield self.visible, chat.count, chat.kind == "basic_group"
        if self.page_error:
            raise self.page_error

    async def resolve_user(self, user_id, username):
        if not self.resolvable:
            raise Problem("unresolved", "No account-specific hash")
        return SimpleNamespace(user_id=user_id)

    async def membership(self, chat, user):
        return self.present

    async def add(self, chat, user):
        self.writes.append(user.user_id)
        if self.add_error:
            raise self.add_error
        if self.after_add:
            self.after_add()
        return self.result

    async def invite_link(self, chat, options):
        self.writes.append("invite-link")
        if self.add_error:
            raise self.add_error
        return "https://t.me/+FICTIONAL_ONLY"


@pytest.fixture
def setup(tmp_path):
    db = Store(tmp_path)
    gateway = Fake()
    db.execute("INSERT INTO mappings VALUES (1,?,?,?,?)", (gateway.source.id, gateway.dest.id, "old", "new"))
    yield db, gateway
    db.close()


def seed(db, gw, user=None, consent=True):
    member = user or gw.visible[0]
    db.put_member(member, gw.source.id)
    if consent:
        db.set_consent(member.id, gw.dest.id, "opted_in", now(), "Fictional explicit opt-in form ref")
    return member.id


def plan(db, gw, mode="direct-add"):
    return arun(make_plan(db, gw, 1, mode))


def test_01_inaccessible_source_has_no_fabricated_records(setup):
    db, gw = setup
    result = arun(collect(db, gw, gw.source.id))
    assert result["status"] == "blocked" and db.rows("SELECT * FROM members") == []


def test_02_new_account_permissions_checked_independently(setup):
    db, gw = setup
    old = Fake()
    old.account_id = 101
    old.source = replace(old.source, account_id=101, admin=True, enumerate=True)
    assert arun(collect(db, old, old.source.id))["retrieved"] == 1
    assert arun(collect(db, gw, gw.source.id))["status"] == "blocked"


def test_03_channel_not_discussion_and_no_indirect_harvesting(setup):
    db, gw = setup
    gw.source = replace(gw.source, admin=True, enumerate=True)
    result = arun(collect(db, gw, gw.source.id))
    assert result["source"] != gw.source.linked_id
    assert {r["source"] for r in db.rows("SELECT source FROM memberships")} == {gw.source.id}


def test_04_partial_visible_subset_and_empty_snapshot(setup):
    db, gw = setup
    gw.source = replace(gw.source, enumerate=True)
    gw.page_error = Problem("permission_blocked", "Restricted on later page")
    assert arun(collect(db, gw, gw.source.id))["status"] == "partial"
    gw.page_error = None
    gw.source = replace(gw.source, count=1)
    assert arun(collect(db, gw, gw.source.id))["status"] == "visible_subset"
    gw.visible = []
    gw.source = replace(gw.source, kind="basic_group", count=0)
    assert arun(collect(db, gw, gw.source.id))["status"] == "empty"


def test_05_deduplicate_preserving_sources(setup):
    db, gw = setup
    user = seed(db, gw)
    db.put_member(gw.visible[0], -10033)
    assert len(db.rows("SELECT * FROM members")) == 1
    assert len(db.rows("SELECT * FROM memberships WHERE user_id=?", (user,))) == 2


def test_06_membership_is_not_consent(setup):
    db, gw = setup
    user = seed(db, gw, consent=False)
    assert db.consent(user, gw.dest.id)["state"] == "unknown"


def test_07_destination_specific_optin_required(setup):
    db, gw = setup
    user = seed(db, gw, consent=False)
    db.set_consent(user, -999, "opted_in", now(), "Different destination form")
    body, digest = plan(db, gw)
    assert not body["eligible"] and body["excluded"][0]["reason"] == "consent_unknown"
    arun(execute(db, gw, body["id"], digest, True))
    assert gw.writes == []


def test_08_revoked_consent_invalidates_pending_plan(setup):
    db, gw = setup
    user = seed(db, gw)
    body, digest = plan(db, gw)
    db.set_consent(user, gw.dest.id, "revoked", now(), "Revoked")
    with pytest.raises(Problem, match="changed_consent"):
        arun(execute(db, gw, body["id"], digest, True))
    assert not gw.writes


def test_consent_checked_again_between_queued_additions(setup):
    db, gw = setup
    seed(db, gw, Member(10, "a", "A"))
    seed(db, gw, Member(20, "b", "B"))
    body, digest = plan(db, gw)
    gw.after_add = lambda: db.set_consent(20, gw.dest.id, "revoked", now(), "Revoked during batch")
    result = arun(execute(db, gw, body["id"], digest, True))
    assert gw.writes == [10]
    assert db.one("SELECT state FROM outcomes WHERE run_id=? AND user_id=20", (result["run_id"],))["state"] == "consent_revoked"


@pytest.mark.parametrize("mode", ["direct-add", "invite-link"])
def test_09_dry_run_no_external_writes(setup, mode):
    db, gw = setup
    seed(db, gw)
    body, digest = plan(db, gw, mode)
    assert arun(execute(db, gw, body["id"]))["external_writes"] == 0
    assert not gw.writes


def test_10_exact_plan_approval_required(setup):
    db, gw = setup
    seed(db, gw)
    body, digest = plan(db, gw)
    with pytest.raises(Problem, match="approval_required"):
        arun(execute(db, gw, body["id"], "yes", True))
    body["mapping"]["destination"] = -123
    db.execute("UPDATE plans SET body=? WHERE id=?", (json.dumps(body), body["id"]))
    with pytest.raises(Problem, match="approval_required"):
        arun(execute(db, gw, body["id"], digest, True))


@pytest.mark.parametrize("change", ["account", "mapping", "permissions"])
def test_11_changed_account_target_or_permissions_invalidates(setup, change):
    db, gw = setup
    seed(db, gw)
    body, digest = plan(db, gw)
    if change == "account":
        gw.account_id = 303
    elif change == "mapping":
        db.execute("UPDATE mappings SET destination=-555 WHERE id=1")
    else:
        gw.dest = replace(gw.dest, invite=False)
    with pytest.raises(Problem, match="changed_plan"):
        arun(execute(db, gw, body["id"], digest, True))


def test_12_privacy_failure_not_retried_by_other_account(setup):
    db, gw = setup
    seed(db, gw)
    gw.result = "privacy_blocked"
    body, digest = plan(db, gw)
    arun(execute(db, gw, body["id"], digest, True))
    gw.account_id = 303
    new, _ = plan(db, gw)
    assert not new["eligible"] and new["excluded"][0]["reason"] == "previous_privacy_blocked"


def test_13_adapter_missing_invitee_is_not_success(setup):
    from telegram_migrate.telegram_client import TelegramGateway
    from telethon.tl import types as T
    db, gw = setup
    adapter = object.__new__(TelegramGateway)
    async def response(request):
        return SimpleNamespace(missing_invitees=[SimpleNamespace(user_id=42)])
    async def peer(identifier):
        return T.InputPeerChannel(22, 123)
    adapter.client, adapter.peer = response, peer
    assert arun(adapter.add(gw.dest, T.InputPeerUser(42, 321))) == "privacy_blocked"


def test_14_flood_wait_persisted_and_blocks_early_resume(setup):
    db, gw = setup
    seed(db, gw)
    body, digest = plan(db, gw)
    gw.add_error = Problem("flood_wait", "Wait", wait=3600)
    result = arun(execute(db, gw, body["id"], digest, True))
    row = db.one("SELECT * FROM runs WHERE id=?", (result["run_id"],))
    assert row["not_before"] >= now() + 3599
    with pytest.raises(Problem, match="flood_wait"):
        arun(execute(db, gw, body["id"], digest, True, result["run_id"]))
    assert len(gw.writes) == 1


def test_spam_halts_all_future_account_operations(setup):
    db, gw = setup
    seed(db, gw)
    body, digest = plan(db, gw)
    gw.add_error = Problem("spam_restricted", "Stop")
    result = arun(execute(db, gw, body["id"], digest, True))
    assert result["status"] == "halted"
    new, new_digest = plan(db, gw)
    with pytest.raises(Problem, match="spam_restricted"):
        arun(execute(db, gw, new["id"], new_digest, True))


def test_15_interrupted_write_reconciled_not_repeated(setup):
    db, gw = setup
    seed(db, gw)
    body, digest = plan(db, gw)
    gw.add_error = Problem("network", "Connection lost after send")
    result = arun(execute(db, gw, body["id"], digest, True))
    gw.add_error = None
    arun(execute(db, gw, body["id"], digest, True, result["run_id"]))
    assert len(gw.writes) == 1
    assert db.one("SELECT state FROM outcomes WHERE run_id=?", (result["run_id"],))["state"] == "unknown"
    gw.present = "present"
    arun(execute(db, gw, body["id"], digest, True, result["run_id"]))
    assert db.one("SELECT state FROM outcomes WHERE run_id=?", (result["run_id"],))["state"] == "already_present"


def test_16_csv_large_ids_and_atomic_validation(setup, tmp_path):
    db, gw = setup
    path = tmp_path / "in.csv"
    path.write_text("user_id,username,display_name,bot,deleted,source_id\n9007199254740993,fictional,Fictional,0,0,-10011\n", encoding="utf-8")
    assert import_members(db, path) == 1
    assert db.one("SELECT id FROM members")["id"] == 9007199254740993
    path.write_text("user_id,username,display_name,bot,deleted,source_id\n5,a,A,0,0,-10011\n1.2e9,b,B,0,0,-10011\n", encoding="utf-8")
    with pytest.raises(Problem, match="Line 3"):
        import_members(db, path)
    assert not db.rows("SELECT * FROM members WHERE id=5")


def test_17_csv_formula_protection_and_round_trip(setup, tmp_path):
    db, gw = setup
    seed(db, gw, Member(10, "@evil", "  =HYPERLINK(\"bad\")"), False)
    path = tmp_path / "out.csv"
    export_members(db, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert rows[0]["display_name"].startswith("'") and rows[0]["username"].startswith("'")
    assert import_members(db, path) == 1
    assert db.one("SELECT display_name FROM members WHERE id=10")["display_name"] == '  =HYPERLINK("bad")'


def test_consent_import_requires_attestation_and_evidence(setup, tmp_path):
    db, gw = setup
    path = tmp_path / "consent.csv"
    path.write_text("user_id,destination_id,state,timestamp,evidence\n42,-10022,opted_in,2026-01-01T00:00:00Z,fictional form reference\n", encoding="utf-8")
    with pytest.raises(Problem, match="attestation_required"):
        import_consent(db, path)
    assert import_consent(db, path, True) == 1
    assert db.consent(42, -10022)["state"] == "opted_in"


def test_18_logs_redact_secrets_and_invites():
    value = secret("private-api-hash")
    record = logging.LogRecord("test", 20, "", 0, "api_hash=%s link=https://t.me/+SECRETTOKEN code=12345", (value,), None)
    Redactor().filter(record)
    assert all(s not in record.getMessage() for s in (value, "SECRETTOKEN", "12345"))


def test_19_credential_files_ignored():
    text = (Path(__file__).parents[1] / ".gitignore").read_text()
    assert "*.session*" in text and ".env" in text and "config.local.toml" in text and "*.sqlite*" in text


def test_20_live_client_calls_blocked():
    from telethon import TelegramClient
    from telethon.sessions import MemorySession
    client = TelegramClient(MemorySession(), 12345, "fictional")
    with pytest.raises(AssertionError, match="forbidden"):
        arun(client.connect())


def test_21_unresolvable_imported_user_excluded(setup):
    db, gw = setup
    seed(db, gw)
    gw.resolvable = False
    body, _ = plan(db, gw)
    assert not body["eligible"] and body["excluded"][0]["reason"] == "unresolved"


def test_22_restricted_source_never_enumerated(setup):
    db, gw = setup
    async def forbidden(chat):
        raise AssertionError("Hidden source must never be enumerated")
        yield
    gw.pages = forbidden
    assert arun(collect(db, gw, gw.source.id))["status"] == "blocked"


def test_membership_unknown_skips_write(setup):
    db, gw = setup
    seed(db, gw)
    body, digest = plan(db, gw)
    gw.present = "unknown"
    arun(execute(db, gw, body["id"], digest, True))
    assert not gw.writes


def test_invite_incompatible_options_rejected_and_link_not_default_output(setup):
    db, gw = setup
    with pytest.raises(Problem, match="cannot be combined"):
        arun(make_plan(db, gw, 1, "invite-link", {"join_request": True, "usage_limit": 2}))
    body, digest = plan(db, gw, "invite-link")
    result = arun(execute(db, gw, body["id"], digest, True))
    assert "link" not in result and "FICTIONAL" not in str(result)


def test_ambiguous_invite_creation_not_repeated(setup):
    db, gw = setup
    body, digest = plan(db, gw, "invite-link")
    gw.add_error = Problem("network", "Lost response")
    result = arun(execute(db, gw, body["id"], digest, True))
    gw.add_error = None
    resumed = arun(execute(db, gw, body["id"], digest, True, result["run_id"]))
    assert resumed["status"] == "unknown" and len(gw.writes) == 1


def test_expired_plan_refresh_voids_approval(setup):
    db, gw = setup
    seed(db, gw)
    body, _ = plan(db, gw)
    body["created_at"] -= 3600
    old = fingerprint(body)
    db.execute("UPDATE plans SET body=?,fingerprint=? WHERE id=?", (json.dumps(body), old, body["id"]))
    with pytest.raises(Problem, match="stale_plan"):
        arun(execute(db, gw, body["id"], old, True))
    _, digest = arun(refresh_plan(db, gw, body["id"]))
    assert digest != old
    with pytest.raises(Problem, match="approval_required"):
        arun(execute(db, gw, body["id"], old, True))


def test_source_delete_preserves_other_membership(setup):
    db, gw = setup
    seed(db, gw)
    db.put_member(gw.visible[0], -10033)
    db.delete_source(gw.source.id)
    assert len(db.rows("SELECT * FROM members")) == 1
    assert len(db.rows("SELECT * FROM memberships")) == 1


def test_worker_lock_prevents_concurrent_execution(tmp_path):
    with worker_lock(tmp_path):
        with pytest.raises(Problem, match="locked"):
            with worker_lock(tmp_path):
                pass
