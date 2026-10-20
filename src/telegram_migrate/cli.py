import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated
import typer
from rich.console import Console
from rich.table import Table
from . import config
from .database import Store
from .models import Problem, now
from .security import configure_logging, redact, worker_lock

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False,
                  help="Local permission-aware Telegram community migration. Network writes require a confirmed saved plan.")
groups = {}
for name in ("auth", "access", "chats", "mappings", "members", "consent", "invites", "announcement", "migration", "data"):
    groups[name] = typer.Typer(no_args_is_help=True)
    app.add_typer(groups[name], name=name)
console = Console(markup=False, highlight=False)
Option = typer.Option
Account = Annotated[str, Option(help="Separate session label: old or new")]


def store():
    return Store(config.home())


def show(value):
    console.print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def table(rows):
    if not rows:
        console.print("No matching local records.")
        return
    view = Table()
    for key in rows[0]:
        view.add_column(key)
    for row in rows:
        view.add_row(*(str(v) for v in row.values()))
    console.print(view)


@asynccontextmanager
async def online(db, label, logging_in=False):
    from .telegram_client import TelegramGateway
    path = config.session_path(label)
    if not logging_in and not path.exists():
        raise Problem("session_missing", f"No {label} session. Use auth login --account {label}")
    with worker_lock(config.home(), f"{label}.session.lock"):
        gateway = TelegramGateway(db, path, *config.credentials())
        try:
            await gateway.connect()
            if not logging_in:
                identity = await gateway.identity()
                console.print(f"Authenticated {label} account: Telegram user ID {identity}")
                bound = db.rows("SELECT id FROM accounts WHERE label=?", (label,))
                if not bound or bound[0]["id"] != identity:
                    raise Problem("identity_changed", "Session identity differs from local registration. Verify it through auth login")
                from .migration import check_restrictions
                check_restrictions(db, identity)
            yield gateway
        finally:
            await gateway.close()


def run(coroutine):
    try:
        return asyncio.run(coroutine)
    except Problem:
        raise
    except Exception as exc:
        from .telegram_client import translate
        raise translate(exc) from None


@groups["auth"].command("login")
def auth_login(account: Account = "new", session: Annotated[Path | None, Option(help="Explicit compatible Telethon session created for this app")] = None):
    """Credentials are requested securely in this terminal, never in chat."""
    from .authentication import login, supply_session
    db = store()
    path = config.session_path(account)
    if session:
        if not typer.confirm("I supplied this compatible Telethon session created for this application. Copy it locally?"):
            raise typer.Abort()
        supply_session(path, session)
    async def action():
        async with online(db, account, True) as gateway:
            identity = await login(gateway)
            console.print(f"Authenticated {account} account: Telegram user ID {identity}")
            if not typer.confirm("Register this verified identity for this label?"):
                raise typer.Abort()
            db.execute("INSERT OR REPLACE INTO accounts VALUES (?,?)", (account, identity))
    run(action())


@groups["auth"].command("status")
def auth_status(verify: Annotated[bool, Option(help="Verify sessions through read-only Telegram calls")] = False):
    db = store()
    table([{**r, "session_present": config.session_path(r["label"]).exists(), "verified_now": False}
           for r in db.rows("SELECT * FROM accounts")])
    if verify:
        async def action():
            for row in db.rows("SELECT * FROM accounts"):
                async with online(db, row["label"]) as gateway:
                    show({"label": row["label"], "verified_id": await gateway.identity()})
        run(action())


@groups["auth"].command("remove-local")
def remove_local(account: Account = "new"):
    """Remove this app's local credential; DOES NOT revoke server-side authorization."""
    path = config.session_path(account)
    if not typer.confirm(f"Delete local {account} session? This does NOT revoke Telegram authorization."):
        raise typer.Abort()
    with worker_lock(config.home()), worker_lock(config.home(), f"{account}.session.lock"):
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        db = store()
        ids = db.rows("SELECT id FROM accounts WHERE label=?", (account,))
        for row in ids:
            db.execute("DELETE FROM refs WHERE account_id=?", (row["id"],))
        db.execute("DELETE FROM accounts WHERE label=?", (account,))
    console.print("Local session removed. To revoke it server-side, use official Telegram Settings > Devices.")


@groups["access"].command("wizard")
def access_wizard():
    console.print("1: Recover/use old account  2: Use new account  3: Reuse an explicitly supplied app session  4: Neither has source access")
    choice = typer.prompt("Choose", type=int)
    if choice in {1, 2}:
        auth_login("old" if choice == 1 else "new", None)
        console.print("Next: chats list, then chats inspect. Each account's permissions are checked independently.")
    elif choice == 3:
        auth_login(typer.prompt("Account label (old/new)"), Path(typer.prompt("Explicit app session path")))
    elif choice == 4:
        console.print("Source blocked. No member list can be fabricated. Ask an existing admin to grant access, or use official recovery. You can still configure a destination and draft an announcement with a voluntarily shared invitation link.")
    else:
        raise Problem("validation", "Choose 1, 2, 3 or 4")


@groups["chats"].command("list")
def chats_list(account: Account = "new"):
    async def action():
        async with online(store(), account) as gateway:
            table([row async for row in gateway.dialogs()])
    run(action())


@groups["chats"].command("inspect")
def chats_inspect(chat: Annotated[str, Option()], account: Account = "new"):
    db = store()
    async def action():
        async with online(db, account) as gateway:
            result = await gateway.inspect(chat)
            show(asdict(result))
            records = db.rows("SELECT * FROM collections WHERE source=? AND account_id=?", (result.id, gateway.account_id))
            show({"enumeration": records[0] if records else "not_attempted"})
    run(action())


@groups["mappings"].command("add")
def mapping_add(source: Annotated[str, Option()], destination: Annotated[str, Option()],
                source_account: str = "new", destination_account: str = "new",
                blocked_source: Annotated[bool, Option(help="Record a negative source ID as unverified/blocked without accessing it")] = False):
    db = store()
    async def action():
        async with online(db, destination_account) as gateway:
            dst = await gateway.inspect(destination)
            show({"destination": asdict(dst)})
        if blocked_source:
            from .exports import integer
            try:
                src = integer(source, "source")
                if src >= 0:
                    raise ValueError("Use a stable negative Telegram chat ID")
            except ValueError as exc:
                raise Problem("validation", str(exc)) from None
            db.execute("INSERT OR REPLACE INTO collections VALUES (?,0,'blocked',?,0,NULL,?)",
                       (src, now(), "Unverified supplied source ID; authorized access/recovery required"))
        else:
            async with online(db, source_account) as gateway:
                chat = await gateway.inspect(source)
                src = chat.id
                show({"source": asdict(chat)})
        if src == dst.id:
            raise Problem("validation", "Source and destination must differ")
        if source_account not in {"old", "new"} or destination_account not in {"old", "new"}:
            raise Problem("validation", "Account labels must be old/new")
        cursor = db.execute("INSERT INTO mappings(source,destination,source_account,destination_account) VALUES (?,?,?,?)",
                            (src, dst.id, source_account, destination_account))
        show(db.mapping(cursor.lastrowid))
    run(action())


@groups["mappings"].command("list")
def mapping_list():
    table(store().rows("SELECT * FROM mappings"))


@groups["members"].command("collect")
def members_collect(mapping: Annotated[int, Option()]):
    from .member_collection import collect
    db = store()
    target = db.mapping(mapping)
    async def action():
        async with online(db, target["source_account"]) as gateway:
            show(await collect(db, gateway, target["source"]))
    run(action())


@groups["members"].command("list")
def members_list(source: int | None = None, search: str = "", consent: str | None = None,
                 destination: int | None = None, state: str | None = None, bot: bool | None = None,
                 deleted: bool | None = None, error: str | None = None):
    from .exports import list_members
    table(list_members(store(), source, search, consent, destination, state, bot, deleted, error))


@groups["members"].command("export")
def members_export(output: Annotated[Path, Option()], source: int | None = None):
    from .exports import export_members
    if output.exists() and not typer.confirm("Overwrite the existing CSV?"):
        raise typer.Abort()
    show({"exported": export_members(store(), output, source), "output": str(output)})


@groups["members"].command("import")
def members_import(input: Annotated[Path, Option(exists=True)]):
    from .exports import import_members
    show({"imported": import_members(store(), input), "consent": "unchanged; membership is not opt-in"})


@groups["consent"].command("import")
def consent_import(input: Annotated[Path, Option(exists=True)], attest: Annotated[bool, Option(help="Attest every row accurately represents destination-specific permission")] = False):
    from .exports import import_consent
    show({"imported": import_consent(store(), input, attest)})


@groups["consent"].command("revoke")
def consent_revoke(user: Annotated[int, Option()], destination: Annotated[int, Option()]):
    store().set_consent(user, destination, "revoked", now(), "Revoked by local operator")
    console.print("Consent revoked for this destination.")


def create_plan(mapping, mode, expires=None, usage_limit=None, join_request=False):
    from .planning import make_plan
    db = store()
    async def action():
        async with online(db, db.mapping(mapping)["destination_account"]) as gateway:
            plan, digest = await make_plan(db, gateway, mapping, mode,
                        {"expires": expires, "usage_limit": usage_limit, "join_request": join_request} if mode == "invite-link" else {})
            show({"plan": plan, "fingerprint": digest, "external_writes": 0})
            console.print("Review this exact saved plan. Execution defaults to dry-run; --apply requires this full fingerprint.")
    run(action())


@groups["invites"].command("plan")
def invites_plan(mapping: Annotated[int, Option()], expires: int | None = None, usage_limit: int | None = None, join_request: bool = False):
    create_plan(mapping, "invite-link", expires, usage_limit, join_request)


@groups["migration"].command("plan")
def migration_plan(mapping: Annotated[int, Option()], mode: str = "invite-link"):
    create_plan(mapping, mode)


def execute_plan(plan_id, apply, confirm, run_id=None, batch_size=5, show_link=False):
    from .migration import execute
    db = store()
    plan, digest = db.plan(plan_id)
    if not apply:
        show(run(execute(db, None, plan_id)))
        return
    # Show the full target before requesting approval, even when fingerprint is supplied.
    show({"plan_id": plan_id, "destination": plan["destination"], "account_id": plan["acting_account_id"],
          "operation": plan["mode"], "eligible_count": len(plan["eligible"]), "fingerprint": digest})
    if not confirm:
        confirm = typer.prompt("Paste the full fingerprint to approve this exact saved plan")
    async def action():
        async with online(db, plan["mapping"]["destination_account"]) as gateway:
            with console.status("Processing sequentially; Ctrl+C pauses safely"):
                result = await execute(db, gateway, plan_id, confirm, True, run_id, batch_size,
                                       progress=lambda n: console.print(f"Processed {n} record(s) in this batch"))
            show(result)
            if show_link and plan["mode"] == "invite-link" and result.get("run_id"):
                row = db.one("SELECT link FROM runs WHERE id=?", (result["run_id"],))
                if row["link"]:
                    console.print(row["link"])
    run(action())


@groups["invites"].command("execute")
def invites_execute(plan: Annotated[str, Option()], apply: bool = False, confirm: str = "", show_link: bool = False):
    if store().plan(plan)[0]["mode"] != "invite-link":
        raise Problem("validation", "Use migration execute for a direct-add plan")
    execute_plan(plan, apply, confirm, show_link=show_link)


@groups["migration"].command("execute")
def migration_execute(plan: Annotated[str, Option()], apply: bool = False, confirm: str = "", batch_size: int = 5):
    execute_plan(plan, apply, confirm, batch_size=batch_size)


@groups["migration"].command("status")
def migration_status(run: Annotated[str, Option()], show_link: bool = False):
    db = store()
    row = db.one("SELECT * FROM runs WHERE id=?", (run,))
    link = row.pop("link", None)
    show(row)
    table(db.rows("SELECT * FROM outcomes WHERE run_id=?", (run,)))
    if show_link and link:
        console.print(link)


@groups["migration"].command("refresh-plan")
def migration_refresh(plan: Annotated[str, Option()]):
    """Revalidate an expired plan for resume; review and approve its NEW fingerprint."""
    from .planning import refresh_plan
    db = store()
    body, _ = db.plan(plan)
    async def action():
        async with online(db, body["mapping"]["destination_account"]) as gateway:
            refreshed, digest = await refresh_plan(db, gateway, plan)
            show({"plan": refreshed, "fingerprint": digest, "external_writes": 0})
    run(action())


@groups["migration"].command("resume")
def migration_resume(run: Annotated[str, Option()], apply: bool = False, confirm: str = "", batch_size: int = 5):
    row = store().one("SELECT * FROM runs WHERE id=?", (run,))
    execute_plan(row["plan_id"], apply, confirm, run, batch_size)


@groups["announcement"].command("draft")
def announcement_draft(mapping: Annotated[int, Option()], link: str = "[DESTINATION INVITATION LINK]"):
    target = store().mapping(mapping)
    console.print(f"Draft for manual review by an authorized administrator of source {target['source']}:\n\n"
                  f"Our community is moving to a new home. If you would like to join, please use this voluntary invitation:\n{link}\n\n"
                  "Joining is optional. Please share this announcement only where you have permission. Thank you!")


@groups["data"].command("delete")
def data_delete(source: Annotated[int, Option()]):
    if not typer.confirm(f"Delete local memberships, related plans/runs and orphaned member data for source {source}?"):
        raise typer.Abort()
    with worker_lock(config.home()):
        store().delete_source(source)
    console.print("Local source data deleted. Other source associations remain. Delete separate exports/backups yourself.")


@groups["data"].command("prune")
def data_prune(older_than_days: Annotated[int, Option(min=1)] = 30):
    db = store()
    sources = db.rows("SELECT source FROM memberships GROUP BY source HAVING MAX(collected_at)<?", (now() - older_than_days * 86400,))
    show({"sources_to_delete": [r["source"] for r in sources]})
    if sources and typer.confirm("Delete these old local source collections and related data?"):
        with worker_lock(config.home()):
            for row in sources:
                db.delete_source(row["source"])


def main():
    configure_logging()
    try:
        app()
    except Problem as exc:
        console.print("Error: " + redact(str(exc)))
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        console.print("Cancelled. Any inflight outcome must be reconciled before retrying.")
        raise SystemExit(130) from None
    except Exception:
        console.print("Operation stopped. No raw exception was logged; check local configuration, dependencies and README troubleshooting.")
        raise SystemExit(1) from None
