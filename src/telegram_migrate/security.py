import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path

_secrets: set[str] = set()


def secret(value: str):
    if value:
        _secrets.add(value)
    return value


def redact(value: str) -> str:
    for item in sorted(_secrets, key=len, reverse=True):
        value = value.replace(item, "[REDACTED]")
    value = re.sub(r"(?:https?://)?(?:t|telegram)\.me/(?:\+|joinchat/)[\w-]+", "[INVITE REDACTED]", value)
    return re.sub(r"(?i)(password|api_hash|login_code|session|code)\s*[=:]\s*\S+", r"\1=[REDACTED]", value)


class Redactor(logging.Filter):
    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        return True


def configure_logging():
    handler = logging.StreamHandler()
    handler.addFilter(Redactor())
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    # Library RPC internals may contain request objects. Do not log them.
    logging.getLogger("telethon").disabled = True


def restrict(path: Path, directory=False):
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        pass  # Windows ACLs are inherited; chmod does not implement POSIX privacy there.


@contextmanager
def worker_lock(home: Path, filename="worker.lock"):
    """OS advisory lock: automatically released on crash, never delete the lock inode."""
    path = home / filename
    with path.open("a+b") as f:
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            from .models import Problem
            raise Problem("locked", "Another local worker is running") from None
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)
