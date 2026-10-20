import os
import tomllib
from pathlib import Path
from .models import Problem
from .security import restrict, secret


def home() -> Path:
    path = Path(os.environ.get("TELEGRAM_MIGRATE_HOME") or Path.home() / ".telegram-migrate").resolve()
    path.mkdir(parents=True, exist_ok=True)
    restrict(path, directory=True)
    return path


def credentials():
    path = Path(os.environ.get("TELEGRAM_MIGRATE_CONFIG", "config.local.toml"))
    cfg = tomllib.loads(path.read_text("utf-8")) if path.exists() else {}
    api_id = os.environ.get("TELEGRAM_API_ID") or cfg.get("api_id")
    api_hash = os.environ.get("TELEGRAM_API_HASH") or cfg.get("api_hash")
    if not api_id or not api_hash:
        raise Problem("configuration", "Set TELEGRAM_API_ID and TELEGRAM_API_HASH locally; see README")
    try:
        api_id = int(api_id)
    except ValueError:
        raise Problem("configuration", "API ID must be an integer") from None
    return api_id, secret(str(api_hash))


def session_path(label: str) -> Path:
    if label not in {"old", "new"}:
        raise Problem("configuration", "Account label must be old or new")
    return home() / f"{label}.session"
