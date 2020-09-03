from asyncio import get_event_loop
from typing import Dict, List, Set

from aiohttp.test_utils import unittest_run_loop
from aiomysql import Pool

from app_google_groups.integrations import GoogleGroupsDatabaseIntegration
from app_google_groups.migrations import ggroups_v1
from app_google_groups.models import GoogleGroup, GoogleGroupMember

from .._helpers import INT_MAX, BaseTestCase
from ._db import get_pool


async def recreate_db(pool: Pool, ggroups_db: GoogleGroupsDatabaseIntegration) -> None:
    async with ggroups_db.get_cursor(write=True) as (conn, cur):
        await cur.execute(f"DROP TABLE IF EXISTS {GoogleGroup.table_name_aliases}")
        await cur.execute(f"DROP TABLE IF EXISTS {GoogleGroupMember.table_name}")
        await cur.execute(f"DROP TABLE IF EXISTS {GoogleGroup.table_name}")
        await ggroups_v1.upgrade(cur)


class TestGoogleGroupsDatabaseIntegration(BaseTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        loop = get_event_loop()
        pool = loop.run_until_complete(get_pool(loop))
        integration = GoogleGroupsDatabaseIntegration(db_conn_pool=pool)
        loop.run_until_complete(recreate_db(pool, integration))

    def setUp(self) -> None:
        super().setUp()
        self.mids: Set[int] = set()
        self.gids: Set[int] = set()

        self.pool: Pool = self.run(get_pool(self.loop))
        self.integration = GoogleGroupsDatabaseIntegration(db_conn_pool=self.pool)
        self.run(self.wipe_db())

    async def wipe_db(self) -> None:
        async with self.integration.get_cursor(write=True) as (conn, cur):
            await cur.execute(f"DELETE FROM {GoogleGroup.table_name}")

    def _generate_aliases(self) -> List[str]:
        return [self._randemail() for _ in range(self.rand.randint(0, 10))]

    def _generate_member(self) -> GoogleGroupMember:
        mid = 1
        while mid in self.mids:
            mid = self.rand.randint(1, INT_MAX)
        self.mids.add(mid)
        return GoogleGroupMember(
            member_id=str(mid),
            email=self._randemail(),
            member_type="USER",
            role=self.rand.choices(["OWNER", "MEMBER"], weights=[1 / 12, 11 / 12], k=1)[0],
            status="ACTIVE",
            etag=self._randstring(128),
            delivery_settings="ALL_MAIL",
        )

    def _generate_group(self) -> GoogleGroup:
        gid = 1
        while gid in self.gids:
            gid = self.rand.randint(1, INT_MAX)
        self.gids.add(gid)
        members = [self._generate_member() for _ in range(self.rand.randint(100, 200))]
        group = GoogleGroup(
            group_id=str(gid),
            name=self._randstring(128),
            email=self._randemail(),
            description=self._randstring(256),
            etag=self._randstring(128),
            aliases=self._generate_aliases(),
            protected=self.rand.random() > 0.5,
        )
        for m in members:
            group.add_member(m)

        return group

    async def check_inserts(self, groups: List[GoogleGroup]) -> None:
        async with self.integration.get_cursor() as (conn, cur):
            await cur.execute(f"SELECT * FROM {GoogleGroup.table_name}")
            assert cur.rowcount == len(groups)
            groups_mapped: Dict[str, GoogleGroup] = {group.group_id: group for group in groups}
            async for row in cur:
                g = GoogleGroup.from_db(row)
                assert g.group_id in groups_mapped
                for field in ["name", "email", "description", "etag", "protected"]:
                    assert getattr(groups_mapped[g.group_id], field) == getattr(g, field)

            for group in groups:
                # Check aliases
                await cur.execute(
                    f"SELECT email FROM {GoogleGroup.table_name_aliases} WHERE group_id = %s",
                    args=(group.group_id,),
                )
                assert cur.rowcount == len(group.aliases)
                async for row in cur:
                    assert row["email"] in group.aliases

                # Check users
                await cur.execute(
                    f"SELECT * FROM {GoogleGroupMember.table_name} WHERE group_id = %s",
                    args=(group.group_id,),
                )
                assert cur.rowcount == len(group.members)
                members_mapped: Dict[str, GoogleGroupMember] = {
                    m.member_id: m for m in group.members
                }
                async for row in cur:
                    m = GoogleGroupMember.from_db(row)
                    assert members_mapped[m.member_id]
                for field in [
                    "email",
                    "member_type",
                    "role",
                    "status",
                    "etag",
                    "delivery_settings",
                ]:
                    assert getattr(members_mapped[m.member_id], field) == getattr(m, field)

    @unittest_run_loop
    async def test_upsert_many(self) -> None:
        groups = [self._generate_group() for _ in range(self.rand.randint(20, 50))]

        await self.integration.upsert_groups(groups)
        await self.check_inserts(groups)

        # Try changing some groups. 1/3 kept the same
        div = len(groups) // 3
        for g in groups[:div]:
            # Replace the aliases
            g.etag = self._randstring(128)
            g.aliases = self._generate_aliases()

        for g in groups[div : div * 2]:
            # Regen the members
            g.etag = self._randstring(128)
            g.members = [self._generate_member() for _ in range(50)]

        await self.integration.upsert_groups(groups)
        await self.check_inserts(groups)

    @unittest_run_loop
    async def test_delete_groups(self) -> None:
        del_group = self._generate_group()
        remaining_group = self._generate_group()
        await self.integration.upsert_groups([del_group, remaining_group])
        await self.check_inserts([del_group, remaining_group])
        await self.integration.delete_groups([del_group.group_id])
        await self.check_inserts([remaining_group])

        try:
            await self.check_inserts([del_group])
        except AssertionError:
            return

        self.fail("Deleted group still in DB")

    @unittest_run_loop
    async def test_get_etags(self) -> None:
        groups = [self._generate_group() for _ in range(self.rand.randint(5, 10))]
        await self.integration.upsert_groups(groups)

        etags = await self.integration.get_etags()
        assert sorted(
            ({"group_id": g.group_id, "etag": g.etag} for g in groups), key=lambda x: x["group_id"]
        ) == sorted(etags, key=lambda x: x["group_id"])

    @unittest_run_loop
    async def test_get_from_id(self) -> None:
        inserted_group = self._generate_group()
        await self.integration.upsert_groups([inserted_group])
        read_group = await self.integration.get_from_id(inserted_group.group_id)

        assert len(read_group.members) == len(inserted_group.members)
        assert len([m.is_owner for m in read_group.members]) == len(
            [m.is_owner for m in inserted_group.members]
        )
        assert inserted_group.email == read_group.email

        assert await self.integration.get_from_id(999) is None

    @unittest_run_loop
    async def test_get_from_email(self) -> None:
        inserted_group = self._generate_group()
        await self.integration.upsert_groups([inserted_group])
        read_group = await self.integration.get_from_email(inserted_group.email)

        assert len(read_group.members) == len(inserted_group.members)
        assert len([m.is_owner for m in read_group.members]) == len(
            [m.is_owner for m in inserted_group.members]
        )

        assert await self.integration.get_from_email("invalid@email.addr") is None

    @unittest_run_loop
    async def test_delete_members(self) -> None:
        group = self._generate_group()
        del_members = group.members[: len(group.members) // 2]
        del_ids = [m.member_id for m in del_members]

        await self.integration.upsert_groups([group])
        await self.check_inserts([group])
        await self.integration.delete_members(group.group_id, del_ids)

        read_group = await self.integration.get_from_id(group.group_id)
        assert len(group.members) // 2 <= len(read_group.members)
        assert not any(m.member_id in del_ids for m in read_group.members)
