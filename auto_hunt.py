import asyncio
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AutoHuntSession:
    """Track running auto-hunt loop for a user and allow safe cleanup."""

    area: Optional[str] = None
    task: Optional[asyncio.Task] = None
    active: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def attach_task(self, task: asyncio.Task, area: str) -> None:
        self.task = task
        self.area = area
        self.active = True

    async def stop(self, reason: str = "") -> None:
        self.active = False
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.area = None

