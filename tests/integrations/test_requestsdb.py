from asyncio import get_event_loop
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from aiohttp.test_utils import unittest_run_loop
from aiomysql import Pool

from app_google_groups.integrations import RequestsDatabaseIntegration
from app_google_groups.migrations import requests_v3
from app_google_groups.models import Request, RequestActions, RequestMessage

from .._helpers import BaseTestCase
from ._db import get_pool


async def recreate_db(pool: Pool, integration: RequestsDatabaseIntegration) -> None:
    async with integration.get_cursor(write=True) as (conn, cur):
        await cur.execute(f"DROP TABLE IF EXISTS {Request.table_name}")
        await requests_v3.upgrade(cur)


class TestRequestsDatabaseIntegration(BaseTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        loop = get_event_loop()
        pool = loop.run_until_complete(get_pool(loop))
        integration = RequestsDatabaseIntegration(db_conn_pool=pool)
        loop.run_until_complete(recreate_db(pool, integration))

    def setUp(self) -> None:
        super().setUp()
        self.pool: Pool = self.run(get_pool(self.loop))
        self.integration = RequestsDatabaseIntegration(db_conn_pool=self.pool)
        self.run(self.wipe_db())

    async def wipe_db(self) -> None:
        async with self.integration.get_cursor(write=True) as (conn, cur):
            await cur.execute(f"DELETE FROM {Request.table_name}")

    def _generate_message(self) -> RequestMessage:
        return RequestMessage(self._randstring(32), self._time_now().timestamp())

    def _generate_request(self, with_approval: bool = False) -> Request:
        messages = [self._generate_message() for _ in range(self.rand.randint(5, 10))]
        reason = None if self._randbool() else self._randstring(25)
        approval_data = (
            [self._randemail(), self._time_now().timestamp(), self._randbool()]
            if with_approval
            else []
        )
        return Request(
            self._randstring(32),
            self._time_now().timestamp(),
            self.rand.choice(
                [
                    RequestActions.BecomeGroupOwner,
                    RequestActions.LeaveGroup,
                    RequestActions.JoinGroup,
                    RequestActions.CreateGroup,
                ]
            ),
            messages,
            [self._randemail() for _ in range(self.rand.randint(1, 100))],
            self._randemail(),
            self._randemail(),
            reason,
            *approval_data,
        )

    async def check_inserts(self, requests: List[Request]) -> None:
        requests_mapped: Dict[str, Request] = {req.request_id: req for req in requests}
        async with self.integration.get_cursor() as (conn, cur):
            await cur.execute(f"SELECT * FROM {Request.table_name}")
            assert cur.rowcount == len(requests)
            async for row in cur:
                req = Request.from_db(row)
                assert req.request_id in requests_mapped
                orig_req = requests_mapped[req.request_id]
                for field in [
                    "request_id",
                    "action",
                    "messages",
                    "requester_email",
                    "group_email",
                    "reason",
                    "approver_email",
                    "approved",
                ]:
                    assert getattr(orig_req, field) == getattr(req, field)
                date = datetime.fromtimestamp(req.timestamp, tz=timezone.utc)
                assert int(date.timestamp()) == orig_req.timestamp
                if orig_req.approval_timestamp:
                    date = datetime.fromtimestamp(req.approval_timestamp, tz=timezone.utc)
                    assert int(date.timestamp()) == orig_req.approval_timestamp

    @unittest_run_loop
    async def test_upsert_request(self) -> None:
        request: Request = self._generate_request()
        await self.integration.upsert_request(request)
        await self.check_inserts([request])

        # Change some stuff, upsert + reverify
        request.add_message(self._generate_message())
        request.reason = self._randstring(64)
        request.approver_email = self._randemail()
        request.approval_timestamp = int(self._time_now().timestamp())
        request.approved = not request.approved
        await self.integration.upsert_request(request)
        await self.check_inserts([request])

    @unittest_run_loop
    async def test_insert_messages(self) -> None:
        request: Request = self._generate_request()
        await self.integration.upsert_request(request)
        await self.check_inserts([request])

        new_messages = [self._generate_message() for _ in range(1, 10)]
        request = await self.integration.insert_messages(
            *new_messages, request_id=request.request_id
        )
        assert all(msg in request.messages for msg in new_messages)
        await self.check_inserts([request])

    @unittest_run_loop
    async def test_update_request_result(self) -> None:
        request: Request = self._generate_request()
        await self.integration.upsert_request(request)
        await self.check_inserts([request])
        approver = self._randemail()
        approved = self._randbool()

        request = await self.integration.update_request_result(
            request.request_id, approver, approved
        )
        assert request.approval_timestamp is not None
        assert request.approver_email == approver
        assert request.approved == approved

    async def _check_get_all(self, requests: List[Request]) -> None:
        count: int = 0
        request_ids: List[str] = [e.request_id for e in requests]
        async for req in self.integration.get_all():
            assert req.request_id in request_ids
            count += 1

        assert count == len(requests)

    @unittest_run_loop
    async def test_get_from_id(self) -> None:
        requests: List[Request] = [self._generate_request() for _ in range(5)]
        for req in requests:
            await self.integration.upsert_request(req)
        await self.check_inserts(requests)

        test_request = requests[self.rand.randint(0, len(requests) - 1)]
        request: Request = await self.integration.get_from_id(test_request.request_id)
        assert request and request.targets == test_request.targets

        request = await self.integration.get_from_id("bla")
        assert request is None

    @unittest_run_loop
    async def test_get_date_range(self) -> None:
        before = self._time_now()
        after = before - timedelta(days=2)

        requests: List[Request] = [self._generate_request() for _ in range(5)]

        # One future, one now, 3 past
        for i, req in enumerate(requests):
            req.timestamp = int((before - timedelta(days=i - 1)).timestamp())

        for req in requests:
            await self.integration.upsert_request(req)
        await self.check_inserts(requests)
        expected_ids = [req.request_id for req in requests[1:-1]]

        # Verify sorting and ids
        last_ts: int = (before + timedelta(days=14)).timestamp()
        async for req in self.integration.get_date_range(before.timestamp(), after.timestamp()):
            assert req.request_id in expected_ids
            assert last_ts > req.timestamp
            last_ts = req.timestamp
