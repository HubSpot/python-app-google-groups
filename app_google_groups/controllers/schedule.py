import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List

from ..integrations import ScheduleDatabaseIntegration
from ..models import ScheduleEvent

CallbackType = Callable[[ScheduleEvent], Awaitable[None]]


class ScheduleController(object):
    def __init__(self, schedule_db: ScheduleDatabaseIntegration) -> None:
        self._schedule_db: ScheduleDatabaseIntegration = schedule_db
        self._schedule: Dict[int, asyncio.Task] = {}
        self._loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self._callbacks: Dict[str, List[CallbackType]] = {}

    async def _handle_event(self, event: ScheduleEvent, delay: float = 0) -> None:
        if delay:
            await asyncio.sleep(delay)
        errors = await asyncio.gather(
            *[callback(event) for callback in self._callbacks.get(event.action_id, [])]
        )
        for error in errors:
            if error is not None and isinstance(error, BaseException):
                print(f"A callback on action ID {event.action_id} failed to run: {error}")
        await self.cancel_event(event.event_id)

    async def _schedule_event(self, event: ScheduleEvent) -> None:
        now = datetime.now(tz=timezone.utc).timestamp()
        if event.timestamp <= now:
            await self._handle_event(event)
        else:
            # Work out how soon the event will happen
            call_delay = event.timestamp - now
            self._schedule[event.event_id] = self._loop.create_task(
                self._handle_event(event, call_delay)
            )

    async def sync(self) -> None:
        async for event in self._schedule_db.get_all():
            if event.event_id not in self._schedule:
                await self._schedule_event(event)

    def register_callback(self, action_id: str, callback: CallbackType) -> None:
        self._callbacks.setdefault(action_id, []).append(callback)

    async def add_event(self, action_id: str, timestamp: int, payload: any) -> ScheduleEvent:
        # We don't even need to schedule the event right now - let the sync handle it
        return await self._schedule_db.add_item(action_id, timestamp, payload)

    async def cancel_event(self, eid: int) -> None:
        await self._schedule_db.delete_item(eid)
        if eid in self._schedule:
            if not self._schedule[eid].cancelled:
                self._schedule[eid].cancel()
            del self._schedule[eid]
