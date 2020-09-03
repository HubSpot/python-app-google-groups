from json import dumps
from typing import AsyncIterator

from aiomysql import Connection, Cursor

from ..models import ScheduleEvent
from ._db import DatabaseIntegration


class ScheduleDatabaseIntegration(DatabaseIntegration):
    async def get_all(
        self, nconn: Connection = None, ncur: Cursor = None
    ) -> AsyncIterator[ScheduleEvent]:
        async with self.get_cursor(nconn, ncur) as (conn, cur):
            await cur.execute(f"SELECT * FROM {ScheduleEvent.table_name}")
            async for row in cur:
                yield ScheduleEvent.from_db(row)

    async def add_item(
        self,
        action_id: str,
        timestamp: int,
        payload: any,
        nconn: Connection = None,
        ncur: Cursor = None,
    ) -> ScheduleEvent:
        timestamp = int(timestamp)
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            await cur.execute(
                f"INSERT INTO {ScheduleEvent.table_name} (action_id, timestamp, payload) "
                "VALUES (%s, %s, %s)",
                args=(action_id, timestamp, dumps(payload),),
            )
            return ScheduleEvent(
                event_id=cur.lastrowid, action_id=action_id, timestamp=timestamp, payload=payload,
            )

    async def delete_item(
        self, event_id: int, nconn: Connection = None, ncur: Cursor = None
    ) -> None:
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            await cur.execute(
                f"DELETE FROM {ScheduleEvent.table_name} WHERE event_id = %s", event_id
            )
