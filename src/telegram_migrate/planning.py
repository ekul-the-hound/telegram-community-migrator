import hashlib
import json
import uuid
from dataclasses import asdict
from .models import Problem, now

PLAN_TTL = 900


def fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_options(options):
    if options.get("join_request") and options.get("usage_limit") is not None:
        raise Problem("validation", "Join requests and usage limits cannot be combined")
    if options.get("usage_limit") is not None and not 1 <= options["usage_limit"] <= 99999:
        raise Problem("validation", "Usage limit must be between 1 and 99999")
    if options.get("expires") is not None and options["expires"] <= now():
        raise Problem("validation", "Expiration must be a future UTC Unix timestamp")


async def snapshot(store, gateway, mapping_id, mode, options=None):
    if mode not in {"direct-add", "invite-link"}:
        raise Problem("unsupported", "Mode must be invite-link or direct-add")
    mapping = store.mapping(mapping_id)
    identity = await gateway.identity()
    destination = await gateway.inspect(mapping["destination"])
    if destination.id != mapping["destination"] or destination.account_id != identity:
        raise Problem("identity_changed", "Resolved destination or account changed")
    if destination.kind not in {"basic_group", "supergroup", "broadcast"}:
        raise Problem("unsupported", "Destination type is unsupported")
    if not (destination.member and destination.admin and destination.invite):
        raise Problem("permission_blocked", "Destination requires membership, administrator role and invite-users permission")
    options = options or {}
    validate_options(options)
    eligible, excluded = [], []
    if mode == "direct-add":
        for row in store.rows("SELECT m.* FROM members m JOIN memberships s ON s.user_id=m.id WHERE s.source=? ORDER BY m.id", (mapping["source"],)):
            user = row["id"]
            consent = store.consent(user, destination.id)
            reason = ""
            prior = store.rows("SELECT o.state FROM outcomes o JOIN runs r ON o.run_id=r.id "
                               "JOIN plans p ON p.id=r.plan_id WHERE o.user_id=? AND "
                               "json_extract(p.body,'$.mapping.destination')=? AND "
                               "o.state IN ('added','already_present','unknown','inflight','privacy_blocked','restricted')",
                               (user, destination.id))
            if row["bot"] or row["deleted"]:
                reason = "bot_or_deleted"
            elif consent["state"] != "opted_in":
                reason = "consent_" + consent["state"]
            elif prior:
                reason = "previous_" + prior[0]["state"]
            else:
                try:
                    await gateway.resolve_user(user, row["username"])
                except Problem as exc:
                    reason = exc.category
            record = {"user_id": user, "consent": consent}
            if reason:
                excluded.append({**record, "reason": reason})
            else:
                eligible.append(record)
    return {"mapping": mapping, "acting_account_id": identity, "destination": asdict(destination),
            "permissions": destination.permissions(), "mode": mode, "options": options,
            "eligible": eligible, "excluded": excluded}


async def make_plan(store, gateway, mapping_id, mode="invite-link", options=None):
    plan = await snapshot(store, gateway, mapping_id, mode, options)
    plan.update(id=uuid.uuid4().hex, created_at=now())
    digest = fingerprint(plan)
    store.save_plan(plan, digest)
    return plan, digest


async def validate_plan(store, gateway, plan_id, confirmation, resuming=False, allow_expired=False):
    plan, digest = store.plan(plan_id)
    if fingerprint(plan) != digest or confirmation != digest:
        raise Problem("approval_required", "Confirmation must match the full fingerprint of this exact saved plan")
    if not allow_expired and not 0 <= now() - plan["created_at"] <= PLAN_TTL:
        raise Problem("stale_plan", "Plan expired (15 minutes); create and review a fresh plan")
    mapping = store.mapping(plan["mapping"]["id"])
    if mapping != plan["mapping"] or await gateway.identity() != plan["acting_account_id"]:
        raise Problem("changed_plan", "Mapping or acting account changed; create a new plan")
    destination = await gateway.inspect(mapping["destination"])
    if destination.permissions() != plan["permissions"]:
        raise Problem("changed_plan", "Destination permissions or resolved identity changed; create a new plan")
    for member in plan["eligible"]:
        if store.consent(member["user_id"], mapping["destination"]) != member["consent"]:
            raise Problem("changed_consent", "Consent changed; create and review a new plan")
    return plan, destination


async def refresh_plan(store, gateway, plan_id):
    """Refresh time only after revalidation; replaces the fingerprint and voids old approval."""
    _, old_digest = store.plan(plan_id)
    plan, _ = await validate_plan(store, gateway, plan_id, old_digest, allow_expired=True)
    plan["created_at"] = max(now(), plan["created_at"] + 1)
    digest = fingerprint(plan)
    store.execute("UPDATE plans SET body=?,fingerprint=? WHERE id=?", (json.dumps(plan), digest, plan_id))
    return plan, digest
