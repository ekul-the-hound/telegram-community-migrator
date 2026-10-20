from .models import Problem, now


async def collect(store, gateway, source):
    seen = set()
    status, explanation, count, snapshot = "blocked", "Enumeration not available", None, False
    try:
        chat = await gateway.inspect(source)
        if chat.account_id != await gateway.identity():
            raise Problem("identity_changed", "Account changed during source check")
        count = chat.count
        if not chat.enumerate:
            raise Problem("permission_blocked", "Member list inaccessible to this account. Ask an authorized admin or use official recovery")
        status = "partial"
        async for members, reported, full_snapshot in gateway.pages(chat):
            count, snapshot = reported, full_snapshot
            before = len(seen)
            for member in members:
                store.put_member(member, source)
                seen.add(member.id)
            if members and len(seen) == before:
                explanation = "Pagination repeated records; stopped with partial result"
                break
        else:
            if chat.kind == "basic_group" and snapshot and count == len(seen):
                status = "empty" if not seen else "complete_snapshot"
                explanation = "Explicit basic-group participant snapshot, at collection time only"
            else:
                status = "visible_subset" if count is None or len(seen) >= count else "partial"
                explanation = "Accessible filtered pages only; count and pagination cannot establish completeness or true emptiness"
    except Exception as exc:
        if isinstance(exc, Problem):
            problem = exc
        else:
            from .telegram_client import translate
            problem = translate(exc)
        status = "partial" if seen else "blocked"
        explanation = str(problem)
        if problem.category == "flood_wait":
            store.execute("INSERT OR REPLACE INTO restrictions VALUES (?,?,0)", (gateway.account_id, now() + problem.wait))
        if problem.category == "spam_restricted":
            store.execute("INSERT OR REPLACE INTO restrictions VALUES (?,0,1)", (gateway.account_id,))
    store.execute("INSERT OR REPLACE INTO collections VALUES (?,?,?,?,?,?,?)",
                  (source, gateway.account_id, status, now(), len(seen), count, explanation))
    return {"source": source, "account_id": gateway.account_id, "status": status,
            "retrieved": len(seen), "reported_count": count, "explanation": explanation}
