from asyncio import Lock
from contextlib import asynccontextmanager
from typing import Tuple

from aiomysql import Connection, Cursor, Pool
from aiomysql.cursors import DeserializationCursor, DictCursor


class DatabaseIntegration(object):
    cursor_type = (DeserializationCursor, DictCursor)

    def __init__(self, db_conn_pool: Pool) -> None:
        self._db_conn_pool: Pool = db_conn_pool
        self._write_lock: Lock = Lock()

    @asynccontextmanager
    async def _get_lock(self, write: bool = False) -> None:
        if write:
            async with self._write_lock:
                yield
        else:
            yield

    @asynccontextmanager
    async def get_cursor(
        self, conn: Connection = None, cur: Cursor = None, write: bool = False
    ) -> Tuple[Connection, Cursor]:
        if conn and cur:
            yield conn, cur
        else:
            async with self._get_lock(write):
                async with self._db_conn_pool.acquire() as conn:
                    async with conn.cursor(*self.cursor_type) as cur:
                        yield conn, cur

                        if write:
                            await conn.commit()

                # https://github.com/aio-libs/aiomysql/issues/449
                if write:
                    await self._db_conn_pool.clear()
