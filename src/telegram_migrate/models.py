from dataclasses import asdict, dataclass
from typing import Protocol
import time


def now() -> int:
    return int(time.time())


class Problem(Exception):
    def __init__(self, category: str, explanation: str = "", wait: int = 0, ambiguous: bool = False):
        self.category, self.explanation, self.wait = category, explanation, wait
        self.ambiguous = ambiguous
        super().__init__(f"{category}: {explanation}" + (f"; wait {wait}s" if wait else ""))


@dataclass
class Chat:
    id: int
    title: str
    kind: str
    account_id: int
    member: bool = False
    admin: bool = False
    invite: bool = False
    post: bool = False
    enumerate: bool = False
    hidden: bool = False
    count: int | None = None
    linked_id: int | None = None

    def permissions(self):
        return {k: v for k, v in asdict(self).items() if k not in {"title", "count"}}


@dataclass
class Member:
    id: int
    username: str = ""
    display_name: str = ""
    bot: bool = False
    deleted: bool = False


class Gateway(Protocol):
    account_id: int
    async def identity(self) -> int: ...
    async def inspect(self, identifier: int | str) -> Chat: ...
    def pages(self, chat: Chat): ...
    async def resolve_user(self, user_id: int, username: str): ...
    async def membership(self, chat: Chat, user) -> str: ...
    async def add(self, chat: Chat, user) -> str: ...
    async def invite_link(self, chat: Chat, options: dict) -> str: ...
