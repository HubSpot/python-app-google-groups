from typing import Dict, List, Optional

from aiomysql import Connection, Cursor
from aiomysql.cursors import DictCursor

from ..models import GoogleGroup, GoogleGroupMember
from ._db import DatabaseIntegration


class GoogleGroupsDatabaseIntegration(DatabaseIntegration):
    cursor_type = (DictCursor,)

    async def _get_one(
        self, filters: str, args: tuple, nconn: Connection = None, ncur: Cursor = None
    ) -> Optional[GoogleGroup]:
        async with self.get_cursor(nconn, ncur) as (conn, cur):
            await cur.execute(
                f"SELECT * FROM {GoogleGroup.table_name} WHERE {filters} LIMIT 1", args=args,
            )
            row = await cur.fetchone()
            if not row:
                return
            group: GoogleGroup = GoogleGroup.from_db(row)

            # Load aliases
            await cur.execute(
                f"SELECT email FROM {GoogleGroup.table_name_aliases} WHERE group_id = %s;",
                args=(group.group_id,),
            )
            group.add_aliases([row["email"] async for row in cur])

            # Load members too
            await cur.execute(
                f"SELECT * FROM {GoogleGroupMember.table_name} WHERE group_id = %s;",
                args=(group.group_id,),
            )
            async for row in cur:
                group.add_member(GoogleGroupMember.from_db(row))

            return group

    async def get_etags(
        self, nconn: Connection = None, ncur: Cursor = None
    ) -> List[Dict[str, str]]:
        """
            Returns a list of (group_id, etag) pairs
        """
        async with self.get_cursor(nconn, ncur) as (conn, cur):
            await cur.execute(f"SELECT group_id, etag FROM {GoogleGroup.table_name}",)
            return await cur.fetchall()

    async def get_from_email(self, email: str) -> Optional[GoogleGroup]:
        return await self._get_one(
            f"email = %s OR group_id = ("
            f"SELECT group_id FROM {GoogleGroup.table_name_aliases} WHERE email = %s LIMIT 1"
            ")",
            (email, email,),
        )

    async def get_from_id(self, group_id: str) -> Optional[GoogleGroup]:
        return await self._get_one("group_id = %s", (group_id,))

    async def get_member_groups(
        self, member_email: str, nconn: Connection = None, ncur: Cursor = None
    ) -> List[GoogleGroup]:
        groups: List[GoogleGroup] = []

        async with self.get_cursor(nconn, ncur) as (conn, cur):
            await cur.execute(
                f"SELECT * FROM {GoogleGroupMember.table_name} WHERE email = %s",
                args=(member_email,),
            )

            # There will be lots of members returned
            members_map: Dict[str, GoogleGroupMember] = {}
            async for row in cur:
                group_id = row["group_id"]
                # Watch out for mutation of row in from_db
                members_map[group_id] = GoogleGroupMember.from_db(row)

            # I'm not going to trust sql to do the right thing on empty filters
            if not len(members_map):
                return groups

            await cur.execute(
                f"SELECT * FROM {GoogleGroup.table_name} "
                f"WHERE group_id IN ({', '.join(['%s'] * len(members_map))})",
                args=tuple(members_map.keys()),
            )
            async for row in cur:
                group = GoogleGroup.from_db(row)
                group.add_member(members_map[group.group_id])
                groups.append(group)

        return groups

    async def upsert_groups(
        self, groups: List[GoogleGroup], nconn: Connection = None, ncur: Cursor = None
    ) -> None:
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            # Using replace will cause existing groups that are being updated to be
            # deleted. This will cascade into the group members table and delete all
            # the associated members too, which we want because doing a diff on members
            # is not feasible, it's faster to re-insert
            await cur.executemany(
                f"REPLACE INTO {GoogleGroup.table_name} "
                "(group_id, name, email, description, etag, protected) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                args=[
                    (g.group_id, g.name, g.email, g.description, g.etag, g.protected,)
                    for g in groups
                ],
            )

            # Insert/Re-insert aliases
            await cur.executemany(
                f"INSERT INTO {GoogleGroup.table_name_aliases} "
                "(email, group_id) "
                "VALUES (%s, %s)",
                args=[(alias, g.group_id) for g in groups for alias in g.aliases],
            )

            # Insert/Re-insert members
            for g in groups:
                await self.upsert_members(g.group_id, g.members, conn, cur)

            # That's enough for one session. Commit now if connection is reused
            if ncur:
                await conn.commit()

    async def upsert_members(
        self,
        group_id: str,
        members: List[GoogleGroupMember],
        nconn: Connection = None,
        ncur: Cursor = None,
    ) -> None:
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            await cur.executemany(
                f"REPLACE INTO {GoogleGroupMember.table_name} "
                "(member_id, group_id, email, member_type, "
                "role, status, etag, delivery_settings) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                args=[
                    (
                        m.member_id,
                        group_id,
                        m.email,
                        m.member_type,
                        m.role,
                        m.status,
                        m.etag,
                        m.delivery_settings,
                    )
                    for m in members
                ],
            )

    async def delete_groups(
        self, group_ids: List[str], nconn: Connection = None, ncur: Cursor = None
    ) -> None:
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            await cur.executemany(
                f"DELETE FROM {GoogleGroup.table_name} WHERE group_id = %s",
                args=[(gid,) for gid in group_ids],
            )

    async def delete_members(
        self, group_id: str, member_ids: List[str], nconn: Connection = None, ncur: Cursor = None,
    ) -> None:
        async with self.get_cursor(nconn, ncur, True) as (conn, cur):
            await cur.executemany(
                f"DELETE FROM {GoogleGroupMember.table_name} "
                "WHERE group_id = %s AND member_id = %s",
                args=[(group_id, mid) for mid in member_ids],
            )
