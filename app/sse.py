import asyncio
import json


class SSEBroadcaster:
    def __init__(self):
        self._queues: set = set()

    def connect(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self._queues.add(q)
        return q

    def disconnect(self, q: asyncio.Queue):
        self._queues.discard(q)

    async def broadcast(self, event_type: str, payload: dict):
        data = json.dumps({"type": event_type, "payload": payload})
        dead = set()
        for q in self._queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.add(q)
        for q in dead:
            self.disconnect(q)


sse_broadcaster = SSEBroadcaster()
