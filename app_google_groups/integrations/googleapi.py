from asyncio import Lock, ensure_future, gather, sleep
from contextlib import asynccontextmanager, contextmanager
from random import random
from typing import AsyncIterable, Dict, Optional, Set

from aiogoogle import HTTPError

from ..aiogoogle_service_account import AiogoogleServiceAccount
from ..config import ConfigSchema
from ..helpers import dict_drop_blanks
from ..models import GoogleGroup, GoogleGroupMember


class GoogleAPIIntegration(object):
    def __init__(
        self, config: ConfigSchema, aiogoogle: AiogoogleServiceAccount = AiogoogleServiceAccount
    ) -> None:
        self._client_creds = config.google._asdict()
        self._aiogoogle: AiogoogleServiceAccount = aiogoogle
        self._domain = config.domain
        self._lock = Lock()

        self._admin_api = None
        self._groups_api = None

        # Etag for the users.list endpoint
        # Optimises performance when user aliases aren't updated
        self._users_etag = None

        # Allow up to 5 requests every 335ms
        # Sums to ~15 per second, 1500 per 100 seconds
        # Rate limit on google is 1500 per 100 seconds
        # There is no race condition on these ints, because
        # asyncio is not threaded or multi process
        self._request_rate = 5
        self._request_interval = 0.335
        self._request_credits = 0
        self._spent_credits = 0

        # Track exponential backoff
        self._pause_interval = 1
        self._pause_max = 30
        self._run_regulator = False

    async def _pressure_regulator(self) -> None:
        self._request_credits = 0
        self._spent_credits = 0
        while self._run_regulator:
            await sleep(self._request_interval)
            self._request_credits += self._request_rate

    @contextmanager
    def pressure_regulator(self) -> any:
        self._run_regulator = True
        ensure_future(self._pressure_regulator())
        try:
            yield
        finally:
            self._run_regulator = False
            print("Google Groups Integration ran", self._spent_credits, "requests")

    async def _spend_credits(self, amount: int = 1) -> None:
        while self._request_credits < amount:
            # Add some variance so that if there's a bunch of requests
            # they don't run at the same time
            await sleep(self._pause_interval + (random() * 3))
            # Check now if the credits have increased enough. If not, wait longer
            if self._request_credits < amount:
                self._pause_interval = min(self._pause_interval ** 2, self._pause_max)
        self._request_credits -= amount
        self._spent_credits += amount

    @asynccontextmanager
    async def get_client(self, client: AiogoogleServiceAccount = None) -> AiogoogleServiceAccount:
        if client:
            yield client
        else:
            # Hacky check to inform console that the API is locked
            # Useful when there are bad crashes
            if self._run_regulator:
                print("Waiting for Google API lock")
            async with self._lock:
                with self.pressure_regulator():
                    async with self._aiogoogle(client_creds=self._client_creds) as aiog:

                        # Cache the API discovery
                        if self._admin_api is None or self._groups_api is None:
                            await self._spend_credits(4)
                            self._admin_api = await aiog.discover("admin", "directory_v1")
                            self._groups_api = await aiog.discover("groupssettings", "v1")

                        yield aiog

    async def _load_members(self, aiog: AiogoogleServiceAccount, group: GoogleGroup) -> None:
        await self._spend_credits(1)
        response = await aiog.as_user(
            self._admin_api.members.list(groupKey=group.group_id), full_res=True
        )

        async for page in response:
            await self._spend_credits(1)

            # If it has no members, the key doesn't even exist :scream:
            for raw_member in page.get("members", []):
                group.add_member(GoogleGroupMember.from_api(raw_member))

    async def _get_protected_status(
        self, aiog: AiogoogleServiceAccount, group: GoogleGroup
    ) -> None:
        await self._spend_credits(1)
        try:
            response = await aiog.as_user(
                self._groups_api.groups.get(groupUniqueId=group.email, alt="json")
            )
            group.protected = response["whoCanJoin"] == "INVITED_CAN_JOIN"
        except HTTPError as err:
            # Rate limiting
            if err.res.status_code == 403:
                self._request_credits = 0
                sleep(self._pause_interval)
                return self._get_protected_status(aiog, group)
            else:
                print(f"Error getting group '{group.email}' protected status: ", err)

    async def load_groups(
        self, etags: Dict[str, str] = None, client: AiogoogleServiceAccount = None
    ) -> AsyncIterable[GoogleGroup]:
        etags = etags or {}

        async with self.get_client(client) as aiog:

            # Load all groups in the domain
            await self._spend_credits(1)
            response = await aiog.as_user(
                self._admin_api.groups.list(domain=self._domain), full_res=True
            )

            # Parse them into GoogleGroup objects
            async for page in response:
                await self._spend_credits(1)

                for raw_group in page["groups"]:
                    group = GoogleGroup.from_api(raw_group)

                    # Check the etag
                    if group.etag != etags.get(group.group_id):
                        # Load members and settings
                        # No point doing this in one big gather because Google <3 rate limits
                        await gather(
                            self._load_members(aiog, group), self._get_protected_status(aiog, group)
                        )

                    yield group

    async def load_user_emails(
        self, client: AiogoogleServiceAccount = None
    ) -> AsyncIterable[Set[str]]:

        async with self.get_client(client) as aiog:

            # Load all users in the domain
            # The emails field is all that is needed.
            # It includes primaryEmail and aliases
            await self._spend_credits(1)
            response = await aiog.as_user(
                self._admin_api.users.list(
                    domain=self._domain,
                    fields="etag,users(emails(address))",
                    projection="full",
                    maxResults=200,
                ),
                full_res=True,
            )

            new_etag: str = ""
            async for page in response:
                await self._spend_credits(1)
                new_etag = new_etag or page["etag"]

                if page["etag"] == self._users_etag:
                    return

                for raw_user in page["users"]:
                    yield {
                        email["address"]
                        for email in raw_user["emails"]
                        if "test-google" not in email["address"]
                    }

            self._users_etag = new_etag

    async def add_group_member(
        self,
        group: GoogleGroup,
        email: str,
        role: str = "MEMBER",
        client: AiogoogleServiceAccount = None,
    ) -> Optional[GoogleGroupMember]:
        async with self.get_client(client) as aiog:

            # Add the member
            try:
                await self._spend_credits(1)
                response = await aiog.as_user(
                    self._admin_api.members.insert(
                        groupKey=group.group_id,
                        json={
                            "email": email,
                            "role": role,
                            "kind": "admin#directory#member",
                            "status": "ACTIVE",
                        },
                    )
                )
            except HTTPError as err:
                if (
                    "member already exists"
                    in err.res.content.get("error", {}).get("message", "").lower()
                ):
                    return
                raise err

            if not response:
                raise ValueError("Member insert request failed: Empty response")

            return GoogleGroupMember.from_api(response)

    async def remove_group_member(
        self, group: GoogleGroup, member: GoogleGroupMember, client: AiogoogleServiceAccount = None,
    ) -> Optional[str]:
        async with self.get_client(client) as aiog:

            # Remove the member
            # TODO idempotent
            await self._spend_credits(1)
            response = await aiog.as_user(
                self._admin_api.members.delete(groupKey=group.group_id, memberKey=member.member_id)
            )

            # Returns None..on success.
            if response and response.status_code >= 299:
                raise ValueError(response.reason or f"Status code {response.status_code}")

    async def add_group(
        self,
        email: str,
        name: str = "",
        description: str = "",
        protect: bool = True,
        client: AiogoogleServiceAccount = None,
    ) -> GoogleGroup:
        async with self.get_client(client) as aiog:

            # Add the group
            await self._spend_credits(1)
            response = await aiog.as_user(
                self._admin_api.groups.insert(
                    json=dict_drop_blanks(
                        {"email": email, "name": name, "description": description}
                    ),
                )
            )

            if not response:
                raise ValueError("Group creation request failed: Empty response")

            group = GoogleGroup.from_api(response)
            who_can_join = "INVITED_CAN_JOIN" if protect else "ALL_IN_DOMAIN_CAN_JOIN"

            await self._spend_credits(1)
            response = await aiog.as_user(
                self._groups_api.groups.patch(
                    groupUniqueId=email, json={"whoCanJoin": who_can_join}
                )
            )

            if not response:
                raise ValueError("Group creation request failed: Failed to set protected status")

            return group
