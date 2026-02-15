from asyncio import Queue
from typing import Protocol, Any


class Broadcastable(Protocol):
    broadcast_queue: Queue[tuple[str, dict]]

    async def broadcast_gds(self, path: str, mode: str) -> None: ...

    async def broadcast_downloader(self, path: str, item: str) -> None: ...

    def encrypt(self, content: str, key: str) -> str: ...

    def decrypt(self, encoded: str, key: str) -> str: ...
