from asyncio import get_event_loop
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from aiohttp.test_utils import unittest_run_loop
from aiomysql import Pool

from app_google_groups.integrations import ScheduleDatabaseIntegration
from app_google_groups.migrations import schedule_v1
from app_google_groups.models import ScheduleEvent

from .._helpers import BaseTestCase
from ._db import get_pool


async def recreate_db(pool: Pool, integration: ScheduleDatabaseIntegration) -> None:
    async with integration.get_cursor(write=True) as (conn, cur):
        await cur.execute(f"DROP TABLE IF EXISTS {ScheduleEvent.table_name}")
        await schedule_v1.upgrade(cur)


class TestScheduleDatabaseIntegration(BaseTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        loop = get_event_loop()
        pool = loop.run_until_complete(get_pool(loop))
        integration = ScheduleDatabaseIntegration(db_conn_pool=pool)
        loop.run_until_complete(recreate_db(pool, integration))

    def setUp(self) -> None:
        super().setUp()
        self.pool: Pool = self.run(get_pool(self.loop))
        self.integration = ScheduleDatabaseIntegration(db_conn_pool=self.pool)
        self.run(self.wipe_db())

    async def wipe_db(self) -> None:
        async with self.integration.get_cursor(write=True) as (conn, cur):
            await cur.execute(f"DELETE FROM {ScheduleEvent.table_name}")

    def _generate_event_data(self) -> Tuple[int, float, any]:
        return (
            self._randstring(128),
            self._time_now().timestamp(),
            {
                "test": self._randstring(128),
                "boolean": self.rand.randint(1, 10) > 5,
                "number": self.rand.randint(0, 5000),
            },
        )

    async def check_inserts(self, events: List[ScheduleEvent]) -> None:
        events_mapped: Dict[str, ScheduleEvent] = {evt.event_id: evt for evt in events}
        async with self.integration.get_cursor() as (conn, cur):
            await cur.execute(f"SELECT * FROM {ScheduleEvent.table_name}")
            assert cur.rowcount == len(events)
            async for row in cur:
                evt = ScheduleEvent.from_db(row)
                assert evt.event_id in events_mapped
                for field in ["event_id", "action_id", "payload"]:
                    assert getattr(events_mapped[evt.event_id], field) == getattr(evt, field)
                date = datetime.fromtimestamp(evt.timestamp, tz=timezone.utc)
                assert int(date.timestamp()) == events_mapped[evt.event_id].timestamp

    @unittest_run_loop
    async def test_add_item(self) -> None:
        event: ScheduleEvent = await self.integration.add_item(*self._generate_event_data())

        assert isinstance(event, ScheduleEvent)
        assert isinstance(event.event_id, int)
        await self.check_inserts([event])

    @unittest_run_loop
    async def test_delete_item(self) -> None:
        saved_event: ScheduleEvent = await self.integration.add_item(*self._generate_event_data())
        deleted_event: ScheduleEvent = await self.integration.add_item(*self._generate_event_data())

        await self.check_inserts([saved_event, deleted_event])

        await self.integration.delete_item(deleted_event.event_id)
        await self.check_inserts([saved_event])

        try:
            await self.check_inserts([deleted_event])
        except AssertionError:
            return

        self.fail("Deleted event still in DB")

    async def _check_get_all(self, events: List[ScheduleEvent]) -> None:
        count: int = 0
        action_ids: List[str] = [e.action_id for e in events]
        async for evt in self.integration.get_all():
            assert evt.action_id in action_ids
            count += 1

        assert count == len(events)

    @unittest_run_loop
    async def test_get_all(self) -> None:
        events: List[ScheduleEvent] = [
            await self.integration.add_item(*self._generate_event_data()) for _ in range(50)
        ]
        await self.check_inserts(events)
        await self._check_get_all(events)

        sliced = events[: len(events) // 2]
        for evt in sliced:
            await self.integration.delete_item(evt.event_id)
        await self._check_get_all(events[len(events) // 2 :])
