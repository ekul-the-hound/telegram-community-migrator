import socket
import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Live network calls are forbidden during tests")
    monkeypatch.setattr(socket, "create_connection", deny)
    from telethon import TelegramClient
    async def deny_connect(*args, **kwargs):
        deny()
    monkeypatch.setattr(TelegramClient, "connect", deny_connect)
