import asyncio
from types import SimpleNamespace
import pytest
from telethon import errors
from telethon.tl import functions as F, types as T
from telethon.tl.custom import ParticipantPermissions
from telegram_migrate.database import Store
from telegram_migrate.models import Chat, Problem
from telegram_migrate.telegram_client import TelegramGateway, translate


def run(value):
    return asyncio.run(value)


@pytest.fixture
def adapter(tmp_path):
    db = Store(tmp_path)
    gateway = object.__new__(TelegramGateway)
    gateway.account_id, gateway.store = 202, db
    yield gateway
    db.close()


def test_inspect_real_telethon_group_types(adapter):
    entity = T.Channel(22, "Fictional group", T.ChatPhotoEmpty(), None, megagroup=True, access_hash=888)
    permissions = ParticipantPermissions(T.ChannelParticipantCreator(202, T.ChatAdminRights(invite_users=True)), False)
    class Client:
        async def get_entity(self, peer):
            return entity
        async def get_permissions(self, entity, who):
            assert who == "me"
            return permissions
        async def __call__(self, request):
            assert isinstance(request, F.channels.GetFullChannelRequest)
            return SimpleNamespace(full_chat=SimpleNamespace(can_view_participants=True, participants_hidden=False, participants_count=10, linked_chat_id=33))
    adapter.client = Client()
    adapter.store.remember(202, -1000000000022, "channel", 888)
    result = run(adapter.inspect(-1000000000022))
    assert result.kind == "supergroup" and result.admin and result.invite and result.post and result.enumerate
    assert result.linked_id == 33 and result.count == 10


def test_broadcast_public_posts_do_not_grant_subscribers(adapter):
    entity = T.Channel(22, "Fictional channel", T.ChatPhotoEmpty(), None, broadcast=True, access_hash=888)
    class Client:
        async def get_entity(self, peer):
            return entity
        async def get_permissions(self, entity, who):
            return ParticipantPermissions(T.ChannelParticipant(202, None), False)
        async def __call__(self, request):
            return SimpleNamespace(full_chat=SimpleNamespace(can_view_participants=True, participants_hidden=False))
    adapter.client = Client()
    adapter.store.remember(202, -1000000000022, "channel", 888)
    result = run(adapter.inspect(-1000000000022))
    assert result.kind == "broadcast" and not result.admin and not result.enumerate and not result.post


def test_hidden_members_block_nonadmin(adapter):
    entity = T.Channel(22, "Fictional hidden group", T.ChatPhotoEmpty(), None, megagroup=True, access_hash=888)
    class Client:
        async def get_entity(self, peer):
            return entity
        async def get_permissions(self, entity, who):
            return ParticipantPermissions(T.ChannelParticipant(202, None), False)
        async def __call__(self, request):
            return SimpleNamespace(full_chat=SimpleNamespace(can_view_participants=True, participants_hidden=True))
    adapter.client = Client()
    adapter.store.remember(202, -1000000000022, "channel", 888)
    assert not run(adapter.inspect(-1000000000022)).enumerate


def test_paginated_adapter_offsets_and_no_phone_persistence(adapter):
    requests = []
    async def client(request):
        requests.append(request)
        if request.offset == 0:
            user = T.User(42, access_hash=123, first_name="Fictional", phone="NOT_TO_BE_STORED")
            return SimpleNamespace(users=[user], participants=[T.ChannelParticipant(42, None)], count=1)
        return SimpleNamespace(users=[], participants=[], count=1)
    adapter.client = client
    adapter.store.remember(202, -1000000000022, "channel", 888)
    chat = Chat(-1000000000022, "Fictional", "supergroup", 202, enumerate=True)
    async def collect_pages():
        return [page async for page in adapter.pages(chat)]
    pages = run(collect_pages())
    assert [r.offset for r in requests] == [0, 1]
    assert pages[0][0][0].id == 42 and not hasattr(pages[0][0][0], "phone")
    assert all(isinstance(r.filter, T.ChannelParticipantsRecent) for r in requests)
    assert adapter.store.one("SELECT hash FROM refs WHERE peer_id=42")["hash"] == 123


def test_account_scoped_hashes_not_reused(adapter):
    adapter.store.remember(101, 42, "user", 123)
    with pytest.raises(Problem, match="unresolved"):
        run(adapter.resolve_user(42, ""))


def test_changed_username_never_changes_user_identity(adapter):
    class Client:
        async def get_entity(self, name):
            return T.User(99, access_hash=456, first_name="Different person")
    adapter.client = Client()
    with pytest.raises(Problem, match="unresolved"):
        run(adapter.resolve_user(42, "old_username_now_owned_by_someone_else"))


@pytest.mark.parametrize("identifier", ["https://t.me/+FICTIONAL", "https://t.me/joinchat/FICTIONAL", "https://example.com/abc"])
def test_private_links_do_not_join(adapter, identifier):
    with pytest.raises(Problem, match="join_required"):
        run(adapter.peer(identifier))


def test_membership_unknown_is_distinct_from_absent(adapter):
    async def peer(identifier):
        return T.InputPeerChannel(22, 888)
    async def client(request):
        raise errors.ChatAdminRequiredError(request)
    adapter.peer, adapter.client = peer, client
    chat = Chat(-10022, "Fictional", "supergroup", 202)
    assert run(adapter.membership(chat, T.InputPeerUser(42, 123))) == "unknown"


def test_post_add_reconciliation_failure_stays_ambiguous(adapter):
    async def peer(identifier):
        return T.InputPeerChannel(22, 888)
    async def client(request):
        return SimpleNamespace(missing_invitees=[])
    async def membership(chat, user):
        raise errors.FloodWaitError(None, 123)
    adapter.peer, adapter.client, adapter.membership = peer, client, membership
    with pytest.raises(Problem) as captured:
        run(adapter.add(Chat(-10022, "Fictional", "supergroup", 202), T.InputPeerUser(42, 123)))
    assert captured.value.ambiguous and captured.value.wait == 123


@pytest.mark.parametrize("error,category", [
    (errors.UserPrivacyRestrictedError(None), "privacy_blocked"),
    (errors.PeerFloodError(None), "spam_restricted"),
    (errors.ChatAdminRequiredError(None), "permission_blocked"),
    (errors.UserNotMutualContactError(None), "privacy_blocked"),
    (errors.UserChannelsTooMuchError(None), "restricted")])
def test_actual_rpc_error_translation(error, category):
    assert translate(error).category == category
