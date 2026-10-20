import getpass
import shutil
from pathlib import Path
from telethon import errors
from .models import Problem
from .security import restrict, secret

RECOVERY = ("Login stopped. Use Forgot password in an official Telegram app and your configured recovery email, "
            "or contact Telegram support. Recovery/reset availability and waiting periods depend on Telegram. "
            "Do not delete/reset the account to recover communities without understanding the consequences. "
            "This application cannot recover passwords or transfer ownership.")


def supply_session(destination: Path, supplied: Path):
    if not supplied.is_file() or supplied.suffix != ".session":
        raise Problem("validation", "Supply an explicit compatible Telethon .session created for this application")
    if destination.exists():
        raise Problem("validation", "A local session already exists; remove it explicitly first")
    if any(Path(str(supplied) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise Problem("validation", "Close the session's application before copying it")
    shutil.copyfile(supplied, destination)
    restrict(destination)


async def login(gateway):
    client = gateway.client
    if not await client.is_user_authorized():
        phone = secret(getpass.getpass("Telegram account phone (local only): "))
        sent = await client.send_code_request(phone)
        code = secret(getpass.getpass("Telegram login code: "))
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
        except errors.SessionPasswordNeededError:
            password = secret(getpass.getpass("Two-step verification password (blank to stop): "))
            if not password:
                raise Problem("recovery_required", RECOVERY)
            try:
                await client.sign_in(password=password)
            except errors.PasswordHashInvalidError:
                raise Problem("recovery_required", RECOVERY) from None
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
            raise Problem("login_code", "Invalid or expired code. Login stopped; run login again") from None
    return await gateway.identity()
