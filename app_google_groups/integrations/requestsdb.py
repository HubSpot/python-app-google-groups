from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional

from aiomysql import Connection, Cursor

from ..models import Request, RequestMessage
from ._db import DatabaseIntegration


class RequestsDatabaseIntegration(DatabaseIntegration):
    async def get_from_id(
        self, request_id: str, nconn: Connection = None, ncur: Cursor = None
    ) -> Optional[Request]:
        async with self.get_cursor(nconn, ncur) as (conn, cur):
            await cur.execute(
                f"SELECT * FROM {Request.table_name} WHERE request_id = %s", args=(request_id,)
            )
            if cur.rowcount:
                return Request.from_db(await cur.fetchone())

    async def get_date_range(
        self, before: int, after: int, nconn: Connection = None, ncur: Cursor = None
    ) -> AsyncIterator[Request]:
        async with self.get_cursor(nconn, ncur) as (conn, cur):
            await cur.execute(
                f"SELECT * FROM {Request.table_name} WHERE timestamp BETWEEN %s AND %s"
                " ORDER BY timestamp DESC",
                args=(int(after), int(before)),
            )
            async for row in cur:
                yield Request.from_db(row)

    async def upsert_request(
        self, request: Request, nconn: Connection = None, ncur: Cursor = None
    ) -> None:
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            await cur.execute(
                f"INSERT INTO {Request.table_name} "
                "(request_id, timestamp, action, messages, targets, requester_email, "
                "group_email, reason, approver_email, approval_timestamp, approved) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE "
                "reason = %s, messages = %s, approver_email = %s, "
                "approval_timestamp = %s, approved = %s",
                args=(
                    request.request_id,
                    request.timestamp,
                    request.action,
                    request.messages_json,
                    request.targets_json,
                    request.requester_email,
                    request.group_email,
                    request.reason,
                    request.approver_email,
                    request.approval_timestamp,
                    request.approved,
                    request.reason,
                    request.messages_json,
                    request.approver_email,
                    request.approval_timestamp,
                    request.approved,
                ),
            )

    async def insert_messages(
        self,
        *messages: List[RequestMessage],
        request_id: str,
        nconn: Connection = None,
        ncur: Cursor = None,
    ) -> Request:
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            request = await self.get_from_id(request_id, nconn=conn, ncur=cur)
            for message in messages:
                request.add_message(message)
            await cur.execute(
                f"UPDATE {Request.table_name} SET messages = %s WHERE request_id = %s",
                args=(request.messages_json, request_id),
            )
            return request

    async def update_request_result(
        self,
        request_id: str,
        approver_email: str,
        approved: bool,
        nconn: Connection = None,
        ncur: Cursor = None,
    ) -> Request:
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            approval_timestamp = datetime.now(tz=timezone.utc).timestamp()
            await cur.execute(
                f"UPDATE {Request.table_name} SET approver_email = %s, "
                "approval_timestamp = %s, approved = %s "
                "WHERE request_id = %s",
                args=(approver_email, approval_timestamp, approved, request_id,),
            )
            await conn.commit()

            # Get the request back, so that the list of messages can be used by
            # the requester to update users of the result
            return await self.get_from_id(request_id, nconn=conn, ncur=cur)
