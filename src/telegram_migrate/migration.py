"""Sequential, persisted execution. Ambiguous writes are never blindly repeated."""
import uuid
from .models import Problem, now
from .planning import validate_plan
from .security import worker_lock, secret


def problem_from(exc):
    if isinstance(exc, Problem):
        return exc
    # Lazy import keeps business logic and mocked tests independent of Telethon.
    from .telegram_client import translate
    return translate(exc)


def check_restrictions(store, account_id):
    rows = store.rows("SELECT * FROM restrictions WHERE account_id=?", (account_id,))
    if rows and rows[0]["halted"]:
        raise Problem("spam_restricted", "Local account halt is set; resolve restriction through Telegram before explicit local clearance")
    if rows and rows[0]["not_before"] > now():
        raise Problem("flood_wait", "Persisted account wait is still active", rows[0]["not_before"] - now())


async def execute(store, gateway, plan_id, confirmation="", apply=False, run_id=None, batch_size=5, progress=None):
    if not apply:
        plan, digest = store.plan(plan_id)
        return {"dry_run": True, "plan": plan, "fingerprint": digest, "external_writes": 0}
    if not 1 <= batch_size <= 20:
        raise Problem("validation", "Batch size must be 1..20; execution remains sequential")
    with worker_lock(store.home):
        plan, destination = await validate_plan(store, gateway, plan_id, confirmation, resuming=bool(run_id))
        check_restrictions(store, gateway.account_id)
        if run_id:
            run = store.one("SELECT * FROM runs WHERE id=? AND plan_id=?", (run_id, plan_id))
        else:
            existing = store.rows("SELECT * FROM runs WHERE plan_id=?", (plan_id,))
            if existing:
                raise Problem("already_started", "Plan already has a run; use migration resume with that run ID")
            run_id = uuid.uuid4().hex
            store.execute("INSERT INTO runs(id,plan_id,status) VALUES (?,?,?)", (run_id, plan_id, "running"))
            run = store.one("SELECT * FROM runs WHERE id=?", (run_id,))
            for row in plan["eligible"]:
                store.outcome(run_id, row["user_id"], "pending")
        if run["not_before"] > now():
            raise Problem("flood_wait", "Run wait is still active", run["not_before"] - now())
        if run["status"] in {"completed", "halted"}:
            return {"run_id": run_id, "status": run["status"]}
        if plan["mode"] == "invite-link":
            if run["status"] in {"inflight", "unknown"}:
                store.execute("UPDATE runs SET status='unknown' WHERE id=?", (run_id,))
                return {"run_id": run_id, "status": "unknown", "explanation": "Inspect destination links manually; creation will not be repeated"}
            store.execute("UPDATE runs SET status='inflight' WHERE id=?", (run_id,))
            try:
                link = secret(await gateway.invite_link(destination, plan["options"]))
                store.execute("UPDATE runs SET status='completed',link=? WHERE id=?", (link, run_id))
            except Exception as exc:
                problem = problem_from(exc)
                halt(store, gateway.account_id, run_id, problem)
            return {"run_id": run_id, "status": store.one("SELECT status FROM runs WHERE id=?", (run_id,))["status"]}
        processed = 0
        try:
            for item in store.rows("SELECT * FROM outcomes WHERE run_id=? ORDER BY user_id", (run_id,)):
                user_id, state = item["user_id"], item["state"]
                if state not in {"pending", "inflight", "unknown"}:
                    continue
                try:
                    row = store.one("SELECT * FROM members WHERE id=?", (user_id,))
                    user = await gateway.resolve_user(user_id, row["username"])
                    present = await gateway.membership(destination, user)
                    if state in {"inflight", "unknown"}:
                        # A missing response may hide a completed action; do not repeat it.
                        store.outcome(run_id, user_id, "already_present" if present == "present" else "unknown",
                                      "reconciled" if present == "present" else "ambiguous", "Reconciled after interruption; no retry")
                        continue
                    prior = store.rows("SELECT o.state FROM outcomes o JOIN runs r ON r.id=o.run_id "
                                       "JOIN plans p ON p.id=r.plan_id WHERE o.user_id=? AND o.run_id!=? AND "
                                       "json_extract(p.body,'$.mapping.destination')=? AND "
                                       "o.state IN ('inflight','unknown','privacy_blocked','restricted','added','already_present')",
                                       (user_id, run_id, destination.id))
                    if prior:
                        store.outcome(run_id, user_id, "previous_outcome", "review_required", "Another run already attempted this user/destination")
                        continue
                    if present == "present":
                        store.outcome(run_id, user_id, "already_present")
                        continue
                    if present == "unknown":
                        store.outcome(run_id, user_id, "membership_unknown", "permission_blocked", "Membership could not be checked; no addition attempted")
                        continue
                    # Immediately before each write, recheck identity, destination permissions and consent.
                    if await gateway.identity() != plan["acting_account_id"]:
                        raise Problem("identity_changed", "Acting account changed")
                    current = await gateway.inspect(destination.id)
                    if current.permissions() != plan["permissions"]:
                        raise Problem("permission_blocked", "Destination permissions changed")
                    if store.consent(user_id, destination.id)["state"] != "opted_in":
                        store.outcome(run_id, user_id, "consent_revoked", "consent", "Consent is no longer opted in")
                        continue
                    check_restrictions(store, gateway.account_id)
                    store.outcome(run_id, user_id, "inflight")
                    result = await gateway.add(destination, user)
                    if result not in {"added", "already_present", "privacy_blocked", "restricted", "unknown"}:
                        result = "unknown"
                    store.outcome(run_id, user_id, result, "ambiguous" if result == "unknown" else result)
                except Exception as exc:
                    problem = problem_from(exc)
                    if problem.ambiguous or problem.category in {"flood_wait", "spam_restricted", "network", "api_error", "identity_changed", "session_invalid"}:
                        current_state = store.one("SELECT state FROM outcomes WHERE run_id=? AND user_id=?", (run_id, user_id))["state"]
                        ambiguous = problem.ambiguous or current_state == "unknown" or current_state == "inflight" and problem.category not in {"flood_wait", "spam_restricted"}
                        outcome = "unknown" if ambiguous else "pending"
                        store.outcome(run_id, user_id, outcome, problem.category, problem.explanation)
                        halt(store, gateway.account_id, run_id, problem)
                        return {"run_id": run_id, "status": "halted" if problem.category == "spam_restricted" else "paused", "error": str(problem)}
                    store.outcome(run_id, user_id, problem.category, problem.category, problem.explanation)
                    if problem.category == "permission_blocked":
                        store.execute("UPDATE runs SET status='paused' WHERE id=?", (run_id,))
                        return {"run_id": run_id, "status": "paused"}
                processed += 1
                if progress:
                    progress(processed)
                if processed >= batch_size:
                    break
        except (KeyboardInterrupt, BaseException):
            # Includes cancellation; inflight is deliberately retained for reconciliation.
            store.execute("UPDATE runs SET status='paused' WHERE id=?", (run_id,))
            raise
        pending = store.rows("SELECT state FROM outcomes WHERE run_id=? AND state IN ('pending','inflight','unknown')", (run_id,))
        status = "paused" if pending else "completed"
        store.execute("UPDATE runs SET status=? WHERE id=?", (status, run_id))
        return {"run_id": run_id, "status": status}


def halt(store, account, run, problem):
    until = now() + problem.wait if problem.category == "flood_wait" else 0
    if problem.category in {"flood_wait", "spam_restricted"}:
        store.execute("INSERT OR REPLACE INTO restrictions VALUES (?,?,?)", (account, until, int(problem.category == "spam_restricted")))
    state = "halted" if problem.category == "spam_restricted" else "unknown" if problem.category in {"network", "api_error"} else "paused"
    store.execute("UPDATE runs SET status=?,not_before=?,error=? WHERE id=?", (state, until, str(problem), run))
