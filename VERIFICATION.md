# Verification report

Date: 2026-10-20. Python: 3.11. Actual dependencies loaded: Telethon 1.45.0, Typer 0.20.0, Rich 15.0.0, pytest 9.0.3.

## Results

* **75 tests passed in 13.15 seconds**, zero failed, in the final acceptance/adapter/CLI suite.
* `python -m compileall -q src` exited successfully.
* CLI `python -m telegram_migrate --help` displayed all command groups successfully.
* Tests exercised help for all 24 documented subcommands and an offline local-list/announcement flow.
* Real Telethon request signatures, participant permission properties and generated TL types were inspected and used in mocked adapter tests.
* **No live Telegram calls were made.** Real account login, server permissions, session reuse and invitation delivery have not been validated with a live account.

The normal installed-project test command is `python -m pytest -q`. During development, pip's network/install path was unavailable in the host sandbox. Official dependency distributions were downloaded through another available runtime into a temporary, excluded directory and loaded locally. They are not bundled with the application; install dependencies normally using the README commands.

The exact final test invocation used this Python wrapper from the project directory (equivalent to running pytest with temporary dependency/source paths and external pytest plugin autoload disabled):

```python
import sys, os
sys.path[:0] = ['.work/site', 'src']
os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
import pytest
sys.exit(pytest.main([
    '-q', '--basetemp=.work/test-final',
    '--junitxml=.work/test-results.xml'
]))
```

All TelegramClient connections and socket connection helpers are blocked by an autouse test fixture. Mocked tests cover source access/no fabrication, independent accounts, discussion separation, restricted enumeration, deduplication, destination-specific consent, revocation, exact-plan approval, changed account/target/permissions, dry-run zero writes, privacy restrictions, missing invitees, persisted flood waits, spam halts, interrupted-write reconciliation, strict CSV validation and large integer IDs, formula-safe exports, log redaction, ignored credentials, unresolved imported IDs and no indirect harvesting. Additional adapter tests verify pagination, hidden-member checks, account-scoped hashes, changed-username identity checks, private-link refusal, real error-class translation and ambiguous post-add reconciliation failures.

## Files delivered

All new files reside within this independent application folder. Existing CreatorScout files and synced project references were not modified.

* Packaging/configuration: `pyproject.toml`, `.gitignore`, `.env.example`.
* Documentation: `README.md`, `VERIFICATION.md`.
* Python package: `src/telegram_migrate/{__init__,__main__,cli,config,database,models,security,authentication,telegram_client,member_collection,exports,planning,migration}.py`.
* Tests: `tests/conftest.py`, `tests/test_acceptance.py`, `tests/test_adapter.py`, `tests/test_cli.py`.
* Fictional examples: `examples/members.csv`, `examples/consent.csv`.
* Reproducible source-only packager: `scripts/package.py`.

## Scope and remaining limits

The CLI, SQLite state, collection, consent import/revocation, exports, planning, guarded invitation creation/direct additions, resumable outcomes, access wizard, local deletion and retention commands are implemented. There is no GUI, automatic announcement posting, chat joining or private-message fallback. Collection completeness is only established for an explicit basic-group participant snapshot with matching users; channel results remain a visible subset/partial. Unknown direct-add outcomes and ambiguous invite creation require conservative review rather than blind retries. No exactly-once guarantee is claimed.

Authorized source access is required to retrieve member records. Old-account recovery uses Telegram's supported login and official recovery options; this application cannot recover passwords or inaccessible subscriber lists. Telegram's privacy, contact, destination, capacity, API-layer and anti-spam rules can prevent additions even when permission and consent are present. It cannot promise to move every subscriber.
