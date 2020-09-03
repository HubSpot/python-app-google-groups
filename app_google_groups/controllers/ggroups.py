from asyncio import gather
from typing import List, Optional, Set, Union

from ..integrations import GoogleAPIIntegration, GoogleGroupsDatabaseIntegration
from ..models import GoogleGroup, GoogleGroupMember

# Number of groups to write to the database at once.
# This includes their members and aliases, so it can get quite large
SYNC_BATCH_SIZE = 20


class GoogleGroupsController(object):
    def __init__(
        self, ggroups_db: GoogleGroupsDatabaseIntegration, google_api: GoogleAPIIntegration
    ) -> None:
        self._ggroups_db: GoogleGroupsDatabaseIntegration = ggroups_db
        self._google_api: GoogleAPIIntegration = google_api
        self._email_aliases: List[Set[str]] = []

    async def sync(self) -> None:
        refreshed: Set[str] = set()
        unchanged: Set[str] = set()
        async with self._ggroups_db.get_cursor(write=True) as (
            conn,
            cur,
        ), self._google_api.get_client() as client:
            etags = {p["group_id"]: p["etag"] for p in await self._ggroups_db.get_etags(conn, cur)}
            groups_buffer: List[GoogleGroup] = []
            async for group in self._google_api.load_groups(etags, client=client):
                # Check has the group etag changed. This is an optimisation in load_groups
                # where loading extra info will be skipped
                if group.etag == etags.get(group.group_id):
                    unchanged.add(group.group_id)
                    continue
                groups_buffer.append(group)
                refreshed.add(group.group_id)

                if len(groups_buffer) == SYNC_BATCH_SIZE:
                    await self._ggroups_db.upsert_groups(groups_buffer, conn, cur)
                    groups_buffer = []

            if len(groups_buffer) > 0:
                await self._ggroups_db.upsert_groups(groups_buffer, conn, cur)

            removed = set(etags.keys())
            removed.difference_update(refreshed.union(unchanged))
            if removed:
                await self._ggroups_db.delete_groups(removed, conn, cur)

            # Load all the email aliases
            # There's no point storing these in the DB. There is no recursive requests being made
            # Returns nothing if the data hasn't changed
            email_aliases = [
                aliases async for aliases in self._google_api.load_user_emails(client=client)
            ]
            if email_aliases:
                self._email_aliases = email_aliases

        print(
            "Google Groups Sync stats:",
            len(refreshed),
            "inserted/updated;",
            len(removed),
            "removed;",
            len(unchanged),
            "unchanged.",
            "User emails loaded:",
            sum(len(s) for s in email_aliases),
        )

    def get_user_emails(self, email: str) -> Set[str]:
        for alias_set in self._email_aliases:
            if email in alias_set:
                return alias_set
        return {email}

    async def get_user_groups(self, email: str) -> List[GoogleGroup]:
        # Account for user alias emails
        async with self._ggroups_db.get_cursor() as (conn, cur):
            return [
                group
                for email in self.get_user_emails(email)
                for group in await self._ggroups_db.get_member_groups(email, nconn=conn, ncur=cur)
            ]

    async def get_from_email(self, email: str) -> Optional[GoogleGroup]:
        return await self._ggroups_db.get_from_email(email)

    async def get_from_id(self, group_id: str) -> Optional[GoogleGroup]:
        return await self._ggroups_db.get_from_id(group_id)

    async def add_members(self, group: GoogleGroup, emails: List[str], role: str = "MEMBER") -> str:
        async with self._google_api.get_client() as client:
            results: List[Union[BaseException, Optional[GoogleGroupMember]]] = await gather(
                *[
                    self._google_api.add_group_member(group, email, role, client=client)
                    for email in emails
                ],
                return_exceptions=True,
            )

        # Update group with member
        await self._ggroups_db.upsert_members(
            group.group_id, [m for m in results if isinstance(m, GoogleGroupMember)]
        )

        return ". ".join(str(error) for error in results if isinstance(error, BaseException))

    async def remove_member(self, group: GoogleGroup, member: GoogleGroupMember) -> None:
        await self._google_api.remove_group_member(group, member)

        # Update group
        await self._ggroups_db.delete_members(group.group_id, [member.member_id])

    def find_member(self, group: GoogleGroup, email: str) -> Optional[GoogleGroupMember]:
        for alias in self.get_user_emails(email):
            member = group.get_member_from_email(alias)
            if member:
                return member

    async def change_role(
        self, group: GoogleGroup, member: GoogleGroupMember, role: str = "MEMBER"
    ) -> str:
        error = await self.remove_member(group, member)
        if error:
            return None, error

        return await self.add_members(group, [member.email], role)

    async def create(self, email: str, name: str, description: str, protect: bool) -> GoogleGroup:
        group = await self._google_api.add_group(
            email=email, name=name, description=description, protect=protect
        )

        # Add group to cache
        await self._ggroups_db.upsert_groups([group])

        return group
