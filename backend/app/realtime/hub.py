"""In-process WebSocket registry: who is connected to which workspace on THIS replica.

Each connection owns a bounded outbound queue drained by its own writer task, so
one slow browser can never block a broadcast to everyone else. When a queue is
full the connection is flagged `overflowed` and its handler closes it (the
client reconnects and refetches state over REST).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field


@dataclass(eq=False, slots=True)
class Connection:
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    queue: asyncio.Queue[str]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    overflowed: asyncio.Event = field(default_factory=asyncio.Event)

    def offer(self, text: str) -> bool:
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:
            self.overflowed.set()
            return False
        return True


class Hub:
    def __init__(self) -> None:
        self._rooms: dict[uuid.UUID, set[Connection]] = {}

    def join(self, connection: Connection) -> None:
        self._rooms.setdefault(connection.workspace_id, set()).add(connection)

    def leave(self, connection: Connection) -> None:
        room = self._rooms.get(connection.workspace_id)
        if room is None:
            return
        room.discard(connection)
        if not room:
            del self._rooms[connection.workspace_id]

    def connections(self, workspace_id: uuid.UUID) -> set[Connection]:
        return set(self._rooms.get(workspace_id, ()))

    def count(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> int:
        return sum(c.user_id == user_id for c in self._rooms.get(workspace_id, ()))

    def users(self, workspace_id: uuid.UUID) -> list[str]:
        return sorted({str(c.user_id) for c in self._rooms.get(workspace_id, ())})

    def broadcast(self, workspace_id: uuid.UUID, text: str) -> int:
        """Queue `text` for every local connection in the workspace; returns deliveries."""
        return sum(c.offer(text) for c in self.connections(workspace_id))

    @property
    def total(self) -> int:
        return sum(len(room) for room in self._rooms.values())
