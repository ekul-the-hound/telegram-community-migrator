"""The only Telegram API adapter. No joins, messages, scraping or session discovery."""
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlparse
import re
from telethon import TelegramClient, errors, functions as F, types as T, utils
from .models import Chat, Member, Problem
from .security import restrict


def translate(exc):
    name = type(exc).__name__
    if isinstance(exc, (OSError, ConnectionError, TimeoutError)):
        return Problem("network", "Network interruption; write outcome may be unknown")
    if name in {"PeerFloodError", "UserRestrictedError", "FrozenMethodInvalidError"}:
        return Problem("spam_restricted", "Telegram restricted this account. Halt; review in official Telegram support")
    if "Flood" in name or "SlowModeWait" in name:
        return Problem("flood_wait", "Telegram requires a pause", int(getattr(exc, "seconds", 60)))
    if name in {"UserPrivacyRestrictedError", "UserNotMutualContactError", "UserIsBlockedError", "UserBlockedError"}:
        return Problem("privacy_blocked", "Telegram privacy/contact restriction")
    if name == "UserAlreadyParticipantError":
        return Problem("already_present", "Already a participant")
    if name in {"ChatAdminRequiredError", "ChatWriteForbiddenError", "ChannelPrivateError", "ChatForbiddenError"}:
        return Problem("permission_blocked", "Telegram denied access; an authorized administrator may need to grant access")
    if name in {"UserChannelsTooMuchError", "UsersTooMuchError", "UserKickedError", "UserBannedInChannelError"}:
        return Problem("restricted", "Telegram invitation or account limit/restriction")
    if isinstance(exc, errors.UnauthorizedError):
        return Problem("session_invalid", "Session is unauthorized or expired; log in locally again")
    if name in {"ChannelMonoforumUnsupportedError"}:
        return Problem("unsupported", "Telegram does not support this chat type")
    if isinstance(exc, (ValueError, TypeError)) or name in {"UserIdInvalidError", "PeerIdInvalidError", "ChannelInvalidError"}:
        return Problem("unresolved", "Entity reference unavailable or stale for this account")
    return Problem("api_error", "Telegram rejected the request; review permissions and official API documentation")


class TelegramGateway:
    def __init__(self, store, session, api_id, api_hash):
        self.store, self.session = store, session
        self.client = TelegramClient(str(session), api_id, api_hash, flood_sleep_threshold=0,
                                     request_retries=0, connection_retries=0, raise_last_call_error=True)
        # Telethon's default cache stores extra personal fields. Keep only our minimal refs.
        self.client.session.save_entities = False
        self.account_id = 0
        restrict(session)

    async def connect(self):
        await self.client.connect()

    async def close(self):
        await self.client.disconnect()
        restrict(self.session)

    async def identity(self):
        if not await self.client.is_user_authorized():
            raise Problem("session_invalid", "Run auth login for this account")
        user = await self.client.get_me()
        if user.bot:
            raise Problem("unsupported", "Use a user account; bot sessions cannot perform direct additions")
        self.account_id = user.id
        return user.id

    def remember(self, entity):
        if isinstance(entity, T.User):
            if not entity.min and entity.access_hash is not None:
                self.store.remember(self.account_id, entity.id, "user", entity.access_hash)
        elif isinstance(entity, T.Channel):
            if not entity.min and entity.access_hash is not None:
                self.store.remember(self.account_id, utils.get_peer_id(entity), "channel", entity.access_hash)
        elif isinstance(entity, T.Chat):
            self.store.remember(self.account_id, utils.get_peer_id(entity), "chat", 0)

    async def peer(self, identifier):
        if isinstance(identifier, str):
            identifier = identifier.strip()
            if identifier.lstrip("-").isdigit():
                identifier = int(identifier)
            elif identifier.startswith(("http://", "https://", "t.me/", "telegram.me/")):
                url = urlparse(identifier if "://" in identifier else "https://" + identifier)
                path = url.path.strip("/")
                if url.hostname not in {"t.me", "telegram.me"} or not path or "/" in path or path.startswith("+"):
                    raise Problem("join_required", "Private invite links do not grant member access. Join manually in Telegram only if intended, then list dialogs")
                identifier = path
        if isinstance(identifier, int):
            refs = self.store.rows("SELECT * FROM refs WHERE account_id=? AND peer_id=?", (self.account_id, identifier))
            if refs:
                ref = refs[0]
                if ref["kind"] == "channel":
                    raw, _ = utils.resolve_id(identifier)
                    return T.InputPeerChannel(raw, ref["hash"])
                if ref["kind"] == "chat":
                    return T.InputPeerChat(-identifier)
                return T.InputPeerUser(identifier, ref["hash"])
            raise Problem("unresolved", "ID has no access reference for this account. List dialogs or resolve an authorized username first")
        entity = await self.client.get_entity(identifier)
        self.remember(entity)
        return utils.get_input_peer(entity)

    async def dialogs(self):
        async for dialog in self.client.iter_dialogs():
            if isinstance(dialog.entity, (T.Chat, T.Channel)):
                self.remember(dialog.entity)
                yield {"id": dialog.id, "title": dialog.title,
                       "kind": self.kind(dialog.entity), "account_id": self.account_id}

    @staticmethod
    def kind(entity):
        if isinstance(entity, T.Chat):
            return "basic_group"
        if getattr(entity, "gigagroup", False) or getattr(entity, "monoforum", False):
            return "unsupported"
        return "supergroup" if getattr(entity, "megagroup", False) else "broadcast"

    async def inspect(self, identifier):
        peer = await self.peer(identifier)
        entity = await self.client.get_entity(peer)
        if not isinstance(entity, (T.Chat, T.Channel)):
            raise Problem("unsupported", "Select a group or channel, not a user or forbidden chat")
        self.remember(entity)
        kind = self.kind(entity)
        full = await self.client(F.messages.GetFullChatRequest(entity.id)) if kind == "basic_group" else await self.client(F.channels.GetFullChannelRequest(peer))
        fc = full.full_chat
        member = False
        admin = invite = post = False
        try:
            permissions = await self.client.get_permissions(entity, "me")
            participant_rights = getattr(permissions.participant, "banned_rights", None)
            member = not bool(getattr(entity, "left", False) or permissions.has_left or getattr(participant_rights, "view_messages", False))
            admin = bool(permissions.is_admin or permissions.is_creator)
            invite = bool(admin and permissions.invite_users)
            defaults = getattr(entity, "default_banned_rights", None)
            post = bool(permissions.post_messages) if kind == "broadcast" else bool(member and (admin or not (
                getattr(participant_rights, "send_messages", False) or getattr(defaults, "send_messages", False))))
        except errors.UserNotParticipantError:
            pass
        hidden = bool(getattr(fc, "participants_hidden", False))
        can_view = isinstance(getattr(fc, "participants", None), T.ChatParticipants) if kind == "basic_group" else bool(getattr(fc, "can_view_participants", False))
        if kind == "broadcast" and not admin:
            can_view = False
        if hidden and not admin:
            can_view = False
        count = getattr(fc, "participants_count", getattr(entity, "participants_count", None))
        return Chat(utils.get_peer_id(entity), entity.title, kind, self.account_id, member, admin,
                    invite, post, can_view and member, hidden, count, getattr(fc, "linked_chat_id", None))

    async def pages(self, chat):
        peer = await self.peer(chat.id)
        if chat.kind == "basic_group":
            result = await self.client(F.messages.GetFullChatRequest(-chat.id))
            parts = result.full_chat.participants
            if not isinstance(parts, T.ChatParticipants):
                raise Problem("permission_blocked", "Basic group participant snapshot is unavailable")
            ids = {p.user_id for p in parts.participants}
            users = [u for u in result.users if u.id in ids]
            yield self.members(users), len(ids), True
            return
        offset = 0
        while True:
            result = await self.client(F.channels.GetParticipantsRequest(peer, T.ChannelParticipantsRecent(), offset, 200, 0))
            ids = {p.user_id for p in result.participants if hasattr(p, "user_id")}
            users = [u for u in result.users if u.id in ids]
            yield self.members(users), result.count, False
            if not result.participants:
                return
            offset += len(result.participants)

    def members(self, users):
        result = []
        for user in users:
            self.remember(user)
            result.append(Member(user.id, user.username or "", " ".join(filter(None, [user.first_name, user.last_name])),
                                 bool(user.bot), bool(user.deleted)))
        return result

    async def resolve_user(self, user_id, username):
        try:
            return await self.peer(user_id)
        except Problem:
            if username and re.fullmatch(r"@?[A-Za-z][A-Za-z0-9_]{0,31}", username):
                entity = await self.client.get_entity(username)
                if isinstance(entity, T.User) and entity.id == user_id and not entity.min:
                    self.remember(entity)
                    return utils.get_input_peer(entity)
            raise Problem("unresolved", "No account-specific user reference; numeric IDs alone are insufficient") from None

    async def membership(self, chat, user):
        try:
            if chat.kind == "basic_group":
                result = await self.client(F.messages.GetFullChatRequest(-chat.id))
                parts = result.full_chat.participants
                if not isinstance(parts, T.ChatParticipants):
                    return "unknown"
                return "present" if any(p.user_id == user.user_id for p in parts.participants) else "absent"
            result = await self.client(F.channels.GetParticipantRequest(await self.peer(chat.id), user))
            part = result.participant
            if isinstance(part, (T.ChannelParticipantLeft, T.ChannelParticipantBanned)):
                return "unknown"  # A restriction is not permission to re-add.
            return "present"
        except errors.UserNotParticipantError:
            return "absent"
        except errors.ChatAdminRequiredError:
            return "unknown"

    async def add(self, chat, user):
        request = F.messages.AddChatUserRequest(-chat.id, user, 0) if chat.kind == "basic_group" else F.channels.InviteToChannelRequest(await self.peer(chat.id), [user])
        result = await self.client(request)
        # Layer-dependent response: inspect missing invitees before reconciliation.
        if any(i.user_id == user.user_id for i in getattr(result, "missing_invitees", [])):
            return "privacy_blocked"
        # An exception-free call does not by itself establish that the user joined.
        try:
            return "added" if await self.membership(chat, user) == "present" else "unknown"
        except Exception as exc:
            problem = translate(exc)
            problem.ambiguous = True
            raise problem from None

    async def invite_link(self, chat, options):
        expires = datetime.fromtimestamp(options["expires"], timezone.utc) if options.get("expires") else None
        result = await self.client(F.messages.ExportChatInviteRequest(await self.peer(chat.id),
                                  expire_date=expires, usage_limit=options.get("usage_limit"),
                                  request_needed=options.get("join_request") or None, title="Community migration"))
        return result.link
