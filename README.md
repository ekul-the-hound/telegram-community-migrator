# Telegram Community Migrator

A locally runnable Python 3.11+ command-line application for reviewing accessible Telegram members and moving **consenting** people using supported invitation mechanisms. No background service or dashboard is needed. SQLite and session files remain on your computer.

The app makes no Telegram calls until you run a network command. Development and automated tests never authenticate, join, post, create links or add real users.

## Install and first run

Clone this repository, then install it in a virtual environment using Windows PowerShell:

```powershell
git clone https://github.com/ekul-the-hound/telegram-community-migrator.git
cd telegram-community-migrator
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
telegram-migrate --help
telegram-migrate access wizard
```

If activation is disabled, use `.venv\Scripts\python.exe -m telegram_migrate` instead of `telegram-migrate`, and `.venv\Scripts\python.exe -m pip` for installation. On macOS/Linux use `python3 -m venv .venv` and `source .venv/bin/activate`.

If you downloaded a ZIP instead, extract it and open the folder containing `pyproject.toml`; skip the `git clone` and `cd` lines.

Obtain your own application API ID/hash at https://my.telegram.org. Keep them local. The app reads `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and optional `TELEGRAM_MIGRATE_HOME`. `.env.example` describes the variables; the application deliberately does not load `.env` automatically. You may instead create the ignored `config.local.toml`:

```toml
api_id = 12345
api_hash = "REPLACE_LOCALLY_WITH_YOUR_API_HASH"
```

`TELEGRAM_MIGRATE_CONFIG` can point to another local TOML file. The default data directory is `~/.telegram-migrate`. Nothing asks you to send credentials to a chat. Login codes and two-step passwords use hidden local terminal prompts and are never persisted by the application.

```powershell
telegram-migrate auth login --account old
telegram-migrate auth login --account new
telegram-migrate auth status
telegram-migrate auth status --verify
telegram-migrate chats list --account new
telegram-migrate chats inspect --account new --chat @your_source
telegram-migrate chats inspect --account new --chat @your_destination
```

`auth status` is explicitly cached local information; `--verify` checks sessions remotely and displays the currently authenticated user ID. Every other network operation also displays and checks that identity against its registered label. Old/new labels are independent accounts; ownership claims grant no permissions.

## Access and recovery

* **Old account recovered:** run `auth login --account old`, provide Telegram's code and two-step password locally, then list/inspect the source as old. Use `--source-account old --destination-account new` when creating a mapping. Collection under old does not transfer user access hashes to new. The new account must independently resolve each person before direct addition.
* **Only the new account is authorized:** log in as new, list/inspect the source, then create the mapping using new for both roles. Collection obeys the new account's own permissions. Existing admins may need to grant an appropriate role. Public posts do not imply a public subscriber list.
* **An existing session for this application:** `auth login --account old --session C:\explicit\old-app.session`. Close its original process first. The app copies only that explicitly supplied compatible Telethon session after local confirmation. It never scans for credentials, accesses Telegram Desktop stores or converts sessions from other apps.
* **Neither account can enumerate:** collection reports blocked and creates no member records. You may still configure a known negative source ID with `mappings add --source=-1001234567890 --blocked-source --destination @your_destination`, create a destination link with an authorized account and draft an announcement for an existing administrator to post manually. The supplied source ID is explicitly unverified. It does not prove ownership or access.

Blank or incorrect two-step passwords stop login. Use **Forgot password** in an official Telegram client and the recovery email if configured; recovery options and waiting periods depend on Telegram. See https://core.telegram.org/api/srp and https://telegram.org/faq. This application never guesses passwords, invokes account deletion, resets an account, transfers ownership or promises recovery.

Private invitation links are not resolved or joined automatically. Join an intended community manually in official Telegram, then list dialogs. The CLI has no join command, no private-message invitation fallback and no automated announcement posting.

## Configure mappings and collect

```powershell
telegram-migrate mappings add --source @old_community --destination @new_community --source-account old --destination-account new
telegram-migrate mappings list
telegram-migrate members collect --mapping 1
telegram-migrate members list --source=-1001234567890
telegram-migrate members list --search fictional --destination=-1009876543210 --consent opted_in
telegram-migrate members list --destination=-1009876543210 --state privacy_blocked
telegram-migrate members list --destination=-1009876543210 --error unresolved
telegram-migrate members list --bot
telegram-migrate members list --no-deleted
telegram-migrate members export --source=-1001234567890 --output members.csv
telegram-migrate members import --input members.csv
```

Negative IDs use `--source=-100...` (equals sign) to avoid option parsing ambiguity. IDs are resolved and stored with Telegram's marked-ID convention: negative basic group IDs, `-100...` channel/supergroup IDs, positive user IDs. List dialogs separately under each acting account to acquire its own minimal chat access references. Public usernames/links can resolve only what Telegram grants. Mappings retain stable IDs, not mutable usernames. Inspect shows title, type, account, membership/admin status, invite/post/enumeration flags, hidden state, available count, linked discussion ID and collection history.

Basic groups use `messages.getFullChat` and an explicit participant snapshot. `complete_snapshot`/`empty` describe that single snapshot only, with matching returned users and count. Channels/supergroups use paginated `channels.getParticipants` with the Recent filter, never search tricks. Results remain `visible_subset` or `partial` even when counts match. Pagination ending alone never proves completeness. Repeated pages stop with a partial result. No posts, comments, reactions, message authors or discussion members are harvested to reconstruct hidden subscribers. A linked discussion is a separate chat and is never automatically enumerated.

Only user ID, username, display name, bot/deleted flags, source association, timestamps, destination consent and migration outcomes are retained. Minimal account-scoped access hashes are stored as credential-sensitive references; no phone numbers, bios, message histories or full user objects are stored. Telethon's automatic entity persistence is disabled for new app sessions. Reused sessions may already contain data from their original app session; handle them as credentials.

Memberships deduplicate by user ID and retain each source association. CSV member import validates all rows before writing, preserves integer IDs without float conversion, and cannot set consent. CSV exports prefix formula-like fields with an apostrophe; this app can import its own protected exports. Avoid spreadsheet software that rounds large IDs, and keep ID columns as text. Exports contain personal data. Member CSV schema is exactly the header in `examples/members.csv`; source memberships are informational, not access grants.

## Consent

```powershell
telegram-migrate consent import --input actual-consent.csv --attest
telegram-migrate consent revoke --user 123456789 --destination=-1009876543210
```

States: `unknown`, `opted_in`, `declined`, `revoked`. Each consent row is keyed by user ID **and destination ID** and includes an explicit timezone timestamp plus evidence/source reference. Import requires `--attest`, your assertion that the records actually represent permission for each destination. No evidence is invented or verified by the program. The example CSV is fictional, must not be treated as genuine opt-in and must be replaced with your actual records. An old community membership never creates consent.

## Preferred mode: voluntary invitation link

```powershell
telegram-migrate invites plan --mapping 1 --join-request
# Or --expires <future-UTC-Unix-timestamp> and/or --usage-limit 25
telegram-migrate invites execute --plan <saved-plan-id>
# The above is a local dry-run, with ZERO Telegram writes.
telegram-migrate invites execute --plan <saved-plan-id> --apply --confirm <full-fingerprint>
telegram-migrate migration status --run <returned-run-id> --show-link
telegram-migrate announcement draft --mapping 1
```

Destination membership, administrator status and invite permission are required. Join requests cannot be combined with a usage limit; incompatible options are rejected. Expiration must be future; the server remains authoritative about allowed combinations and destination capabilities. New links do not revoke existing links. Link creation itself needs a saved plan and exact-fingerprint confirmation. Links are not displayed by default, including in status; use `--show-link` explicitly on status or invitation execution. The announcement draft uses a placeholder by default. You can supply `--link`, but command-line history may retain it: preferably replace the placeholder in your own editor. Drafts are only local text for an authorized administrator's manual review/posting.

## Direct addition: explicit opt-in only

```powershell
telegram-migrate migration plan --mapping 1 --mode direct-add
telegram-migrate migration execute --plan <saved-plan-id>
telegram-migrate migration execute --plan <saved-plan-id> --apply --confirm <full-fingerprint> --batch-size 5
telegram-migrate migration status --run <run-id>
telegram-migrate migration resume --run <run-id>
telegram-migrate migration resume --run <run-id> --apply --confirm <full-fingerprint> --batch-size 5
```

Read the complete saved preview. A plan includes resolved account/destination/source IDs, mapping, permissions, consent evidence, eligible/excluded users and reasons, creation time and a SHA-256 fingerprint. `--apply` is required for writes. Without `--confirm`, applying prompts you to paste the full fingerprint after displaying the destination/account/operation/count. Any changed plan body, mapping, account, relevant permission or eligible consent invalidates that approval. Plans expire after 15 minutes.

Basic groups use `messages.addChatUser` with zero history forwarding. Supergroups/broadcast channels use `channels.inviteToChannel`. Gigagroups/monoforums and bot acting accounts are unsupported. Administrator permission is a conservative app requirement even where a group might allow ordinary users to invite. API privacy, contact, capacity and anti-spam restrictions remain authoritative.

New acting accounts cannot use old accounts' access hashes. Resolution uses only that verified account's own stored input reference, or an existing username whose returned user ID must match the recorded ID. An imported numeric ID alone is not enough. Resolution failures are explicitly excluded; usernames are never durable identity keys.

Before each addition, the app checks membership, account identity, destination permissions and current destination-specific consent. Already-present users are skipped. Unknown membership is reported and skipped; it is never treated as absent. Privacy-blocked and restricted cases are distinct and not retried through another account. An exception-free response is not assumed successful: `missing_invitees` is checked, then membership is reconciled. Successful membership reconciliation records `added`; otherwise the outcome is `unknown`.

Execution is sequential, with a configurable maximum of 1..20 individual attempts per invocation (default 5), persistent outcomes and a cross-process OS worker lock. There are no concurrent batches, account rotation, proxies or bulk messaging. Batch size makes no safety or delivery guarantee. Ctrl+C pauses; an inflight action is reconciled before further work. Duplicate execution of the same plan requires `resume`, and other runs' successful, restricted, privacy-blocked or ambiguous attempts prevent a duplicate attempt for that user/destination.

## Resume, rate limits and ambiguous results

Flood waits persist an account-wide `not_before` and stop immediately rather than sleeping in the background. The app never retries before Telegram's returned wait expires. After a long pause, refresh an expired plan:

```powershell
telegram-migrate migration refresh-plan --plan <saved-plan-id>
telegram-migrate migration resume --run <run-id> --apply --confirm <NEW-full-fingerprint>
```

Refresh revalidates the original targets/account/permissions/consents, updates its time and invalidates the previous fingerprint. Review the new preview. Changed consent or targets require a new migration plan, which excludes prior ambiguous or successful attempts. `resume` defaults to a dry-run.

Spam/abuse restrictions halt the entire account and are persisted locally. No automated retries or clearance are provided. Resolve this through official Telegram support; do not switch accounts to evade it. Connection loss after sending can leave an unknown outcome. Resume checks membership and marks present users as reconciled; otherwise it retains `unknown` without sending again, even when an absence is reported. Such users stay excluded from new plans. This deliberately favors avoiding duplicate unwanted additions. Review ambiguous cases manually; there is no exactly-once guarantee across network failures. An uncertain invitation-link creation is not repeated: inspect exported links in the official Telegram administrator UI.

## Local security and retention

SQLite is **not encrypted**. The default data directory contains credential-sensitive session files, account-specific access references, member lists, consent evidence, plans, outcomes and private links. Use device encryption and protect backups. The app requests POSIX directory/file modes 0700/0600 where supported. On Windows, chmod does not implement private ACLs: use a private Windows user directory and check its inherited ACLs. No claim of Windows ACL enforcement or encrypted storage is made.

Logs use standard logging with sensitive-value/token redaction; raw Telegram exceptions and RPC internals are not logged. Codes/passwords are transient only. API configuration, state, exports, logs and sessions are excluded from version control. Do not upload your state directory when sharing this project.

```powershell
telegram-migrate data delete --source=-1001234567890
telegram-migrate data prune --older-than-days 30
telegram-migrate auth remove-local --account old
```

Deletion previews require local confirmation. Source deletion removes source membership associations, collection history, related plans/runs/outcomes and unreferenced people/consents/user refs, while preserving people associated with other sources. Retention pruning deletes sources whose latest member collection is older than the threshold. SQLite `secure_delete` and VACUUM reduce retained deleted data, but cannot promise forensic erasure from disks/backups. Delete CSV copies, backups, original supplied sessions and sensitive local config yourself.

`auth remove-local` removes this app's local session and account-specific references only. **It does not revoke the server-side Telegram authorization.** Use official Telegram Settings > Devices to terminate that session if necessary. None of these local commands deletes Telegram accounts, messages, chats or communities.

## Tests and implementation

```powershell
python -m pytest -q
python -m compileall -q src
```

All Telegram behavior in the tests is mocked. An autouse fixture blocks real `TelegramClient.connect` and socket connection helpers. The tests cover the requested access/consent/planning/error/CSV/security cases, API response handling and all documented command help routes. No live Telegram account has been validated. See `VERIFICATION.md` for actual execution results and development setup details.

Source modules: `cli.py` handles Typer/Rich; `config.py`, `security.py`, `authentication.py` handle local configuration/session security; `database.py` handles SQLite; `models.py` defines gateway models; `telegram_client.py` contains the only Telegram API calls; `member_collection.py`, `exports.py`, `planning.py` and `migration.py` provide UI-independent services. `tests/` contains mocked acceptance, adapter and CLI tests. `examples/` contains fictional member and consent CSVs. No GUI, automatic posting, automatic joining, password recovery, hidden-subscriber workaround or guarantee of migrating everyone is implemented.

## Documentation verification — 2026-10-20

The following official method pages and Telethon stable 1.45.0 documentation were opened and checked during implementation (not just search snippets). The installed 1.45.0 request signatures and participant permission properties were also inspected locally. Telegram's live schema and RPC capabilities may change; pinned Telethon behavior and conservative reconciliation are used rather than assuming a newer schema.

| Topic | Official reference and implementation consequence |
| --- | --- |
| Login and two-step verification | https://core.telegram.org/api/auth ; https://core.telegram.org/api/srp ; https://docs.telethon.dev/en/stable/basic/signing-in.html — supported code/password flow; no reset/bypass |
| Session credentials | https://docs.telethon.dev/en/stable/concepts/sessions.html — treat explicitly supplied compatible app sessions as account credentials |
| Chat types and discussion separation | https://core.telegram.org/api/channel — basic groups differ from supergroups/channels; gigagroups cannot accept ordinary invitations; linked discussions are separate |
| Enumeration | https://core.telegram.org/method/channels.getParticipants ; https://core.telegram.org/method/messages.getFullChat — permission-gated pages or explicit basic-group snapshot |
| Hidden participants | https://core.telegram.org/constructor/channelFull — inspect can_view_participants and participants_hidden; no indirect harvesting |
| Direct additions | https://core.telegram.org/method/channels.inviteToChannel ; https://core.telegram.org/method/messages.addChatUser — user-client methods subject to administrator, privacy/contact and capacity restrictions |
| Partial successful responses | https://core.telegram.org/constructor/messages.invitedUsers ; https://core.telegram.org/api/invites — inspect missing_invitees; never mark a whole request successful blindly |
| Membership reconciliation | https://core.telegram.org/method/channels.getParticipant — absence and inaccessible membership are distinct |
| Link creation and join requests | https://core.telegram.org/method/messages.exportChatInvite ; https://core.telegram.org/api/invites — expiration/usage/join-request options, supported combination validation |
| Entity resolution | https://docs.telethon.dev/en/stable/concepts/entities.html — IDs persist across accounts; access hashes do not |
| Flood waits/errors | https://docs.telethon.dev/en/stable/concepts/errors.html — respect seconds, disable automatic sleeps/retries, sanitize errors |

If neither account has the necessary source access, no application can legitimately recover that hidden list through a public username or invitation link. Authorized access, an existing administrator's help or official account recovery is required; voluntary invitations can still be shared manually.
