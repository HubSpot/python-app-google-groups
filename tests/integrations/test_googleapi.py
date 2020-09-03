from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, NamedTuple
from unittest.mock import MagicMock, Mock

from aiohttp.test_utils import unittest_run_loop

from app_google_groups.integrations import GoogleAPIIntegration
from app_google_groups.models import GoogleGroupMember

from .._helpers import TLD, BaseTestCase

FAKE_CLIENT_CREDS = {"hi": "mark"}

GROUP_SETTINGS_STATIC = {
    "kind": "groupsSettings#groups",
    "whoCanJoin": "CAN_REQUEST_TO_JOIN",
    "whoCanViewMembership": "ALL_MEMBERS_CAN_VIEW",
    "whoCanViewGroup": "ALL_MEMBERS_CAN_VIEW",
    "whoCanInvite": "ALL_MANAGERS_CAN_INVITE",
    "whoCanAdd": "ALL_MANAGERS_CAN_ADD",
    "allowExternalMembers": "false",
    "whoCanPostMessage": "ALL_MEMBERS_CAN_POST",
    "allowWebPosting": "true",
    "maxMessageBytes": 26214400,
    "isArchived": "false",
    "archiveOnly": "false",
    "messageModerationLevel": "MODERATE_NONE",
    "spamModerationLevel": "MODERATE",
    "replyTo": "REPLY_TO_IGNORE",
    "customReplyTo": "",
    "includeCustomFooter": "false",
    "customFooterText": "",
    "sendMessageDenyNotification": "false",
    "defaultMessageDenyNotificationText": "",
    "showInGroupDirectory": "true",
    "allowGoogleCommunication": "false",
    "membersCanPostAsTheGroup": "false",
    "messageDisplayFont": "DEFAULT_FONT",
    "includeInGlobalAddressList": "true",
    "whoCanLeaveGroup": "ALL_MEMBERS_CAN_LEAVE",
    "whoCanContactOwner": "ANYONE_CAN_CONTACT",
    "whoCanAddReferences": "NONE",
    "whoCanAssignTopics": "NONE",
    "whoCanUnassignTopic": "NONE",
    "whoCanTakeTopics": "NONE",
    "whoCanMarkDuplicate": "NONE",
    "whoCanMarkNoResponseNeeded": "NONE",
    "whoCanMarkFavoriteReplyOnAnyTopic": "NONE",
    "whoCanMarkFavoriteReplyOnOwnTopic": "NONE",
    "whoCanUnmarkFavoriteReplyOnAnyTopic": "NONE",
    "whoCanEnterFreeFormTags": "NONE",
    "whoCanModifyTagsAndCategories": "NONE",
    "favoriteRepliesOnTop": "true",
    "whoCanApproveMembers": "ALL_MANAGERS_CAN_APPROVE",
    "whoCanBanUsers": "OWNERS_AND_MANAGERS",
    "whoCanModifyMembers": "OWNERS_AND_MANAGERS",
    "whoCanApproveMessages": "OWNERS_AND_MANAGERS",
    "whoCanDeleteAnyPost": "OWNERS_AND_MANAGERS",
    "whoCanDeleteTopics": "OWNERS_AND_MANAGERS",
    "whoCanLockTopics": "OWNERS_AND_MANAGERS",
    "whoCanMoveTopicsIn": "OWNERS_AND_MANAGERS",
    "whoCanMoveTopicsOut": "OWNERS_AND_MANAGERS",
    "whoCanPostAnnouncements": "OWNERS_AND_MANAGERS",
    "whoCanHideAbuse": "NONE",
    "whoCanMakeTopicsSticky": "NONE",
    "whoCanModerateMembers": "OWNERS_AND_MANAGERS",
    "whoCanModerateContent": "OWNERS_AND_MANAGERS",
    "whoCanAssistContent": "NONE",
    "customRolesEnabledForSettingsToBeMerged": "false",
    "enableCollaborativeInbox": "false",
    "whoCanDiscoverGroup": "ALL_IN_DOMAIN_CAN_DISCOVER",
}


class MockGoogleConfig(object):
    @staticmethod
    def _asdict() -> Dict[str, str]:
        return FAKE_CLIENT_CREDS


class MockConfig(NamedTuple):
    google: MockGoogleConfig
    domain: str = TLD


class MockRequestHandler(object):
    def __init__(self) -> None:
        self.handlers: Dict[str, Any] = {}

    def set_handler(self, value: str, returns: Any) -> None:
        self.handlers[value] = returns

    async def handle_request(self, value: str, *args: Any, **kwargs: Any) -> Any:
        if value in self.handlers:
            returns = self.handlers[value]
            if callable(returns):
                return returns()
            return returns
        raise AssertionError(f"Unhandled request {value}")


class TestGoogleAPIIntegration(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.mock_config = MockConfig(google=MockGoogleConfig(),)
        self.mock_admin_api = Mock()
        self.mock_groups_api = Mock()

        self.aiogoogle = aiogoogle = MagicMock()
        aiogoogle.discover = self._mock_discover_handler()

        @asynccontextmanager
        async def mock_aiog(client_creds: Any, *args: Any, **kwargs: Any) -> Any:
            assert client_creds == FAKE_CLIENT_CREDS
            yield aiogoogle

        self.integration: GoogleAPIIntegration = GoogleAPIIntegration(
            config=self.mock_config, aiogoogle=mock_aiog,
        )

    def _mock_discover_handler(self) -> Any:
        async def discover(api: str, version: str) -> str:
            return self.mock_admin_api if api == "admin" else self.mock_groups_api

        return discover

    def _generate_member_raw(self) -> Dict[str, str]:
        return {
            "kind": "admin#directory#member",
            "id": self._randstring(24),
            "etag": f'"{self._randstring(64)}"',
            "email": self._randemail(),
            # 25% chance to be owner
            "role": "OWNER" if self._randbool() and self._randbool() else "MEMBER",
            "type": "USER",
            "status": "ACTIVE",
        }

    def _generate_group_raw(self) -> Dict[str, Any]:
        group = {
            "kind": "admin#directory#group",
            "id": self._randstring(24),
            "etag": f'"{self._randstring(64)}"',
            "email": self._randemail(),
            "name": self._randstring(64),
            "directMembersCount": f"{self.rand.randint(0, 1000)}",
            "description": self._randstring(64),
            "adminCreated": True,
        }
        group["nonEditableAliases"] = [group["email"]]

        # Aliases may or may not exists on the object at all
        if self._randbool():
            group["aliases"] = [self._randemail() for _ in range(self.rand.randint(1, 3))]
        return group

    async def _generate_member_pages(self) -> AsyncIterator[Dict[str, str]]:
        for _ in range(self.rand.randint(1, 3)):
            yield {
                "kind": "admin#directory#members",
                "etag": f'"{self._randstring(64)}"',
                "nextPageToken": self._randstring(64),
                "groups": [self._generate_member_raw() for _ in range(self.rand.randint(1, 5))],
            }

    async def _generate_group_pages(self) -> AsyncIterator[Dict[str, Any]]:
        for _ in range(self.rand.randint(1, 10)):
            yield {
                "kind": "admin#directory#groups",
                "etag": f'"{self._randstring(64)}"',
                "nextPageToken": self._randstring(64),
                "groups": [self._generate_group_raw() for _ in range(self.rand.randint(1, 5))],
            }

    def _generate_group_settings(self) -> Dict[str, Any]:
        return {
            **GROUP_SETTINGS_STATIC,
            "email": self._randemail(),
            "name": self._randstring(64),
            "description": self._randstring(64),
            "whoCanJoin": "INVITED_CAN_JOIN" if self._randbool() else "ALL_IN_DOMAIN_CAN_JOIN",
        }

    @unittest_run_loop
    async def test_load_groups(self) -> None:
        pages = [p async for p in self._generate_group_pages()]
        raw_groups = [g for p in pages for g in p["groups"]]
        handler = MockRequestHandler()
        self.aiogoogle.as_user = handler.handle_request

        # Want the same mock groups here + in the load_groups request handler
        async def groups_page_iterator() -> AsyncIterator[Dict[str, Any]]:
            for page in pages:
                yield page

        # Set up the mock APIs to return specific values
        # which we can use to determine the response
        self.mock_groups_api.groups.get = settings_endpoint = Mock(return_value="groups_get")
        handler.set_handler("groups_get", self._generate_group_settings)
        self.mock_admin_api.groups.list = list_endpoint = Mock(return_value="admin_groups_list")
        handler.set_handler("admin_groups_list", groups_page_iterator)
        self.mock_admin_api.members.list = members_endpoint = Mock(
            return_value="admin_members_list"
        )
        handler.set_handler("admin_members_list", self._generate_member_pages)

        read_groups = [group async for group in self.integration.load_groups()]
        read_ids = [group.group_id for group in read_groups]

        assert list_endpoint.called and list_endpoint.call_args[1]["domain"] == TLD
        assert all(group["id"] in read_ids for group in raw_groups)

        # Spot check the items for errors
        for group in raw_groups[::10]:
            read_group = [g for g in read_groups if g.group_id == group["id"]][0]
            assert all(
                v == group[k]
                for k, v in vars(read_group).items()
                if k in ["name", "email", "description", "etag"]
            )
            assert all(isinstance(member, GoogleGroupMember) for member in read_group.members)
            assert all(alias in read_group.aliases for alias in group.get("aliases", []))
            assert all(alias in read_group.aliases for alias in group.get("nonEditableAliases", []))

        # Now test with etags. No members or property requests should be made
        settings_endpoint.reset_mock()
        list_endpoint.reset_mock()
        members_endpoint.reset_mock()
        etags = {g.group_id: g.etag for g in read_groups}
        new_read = [group async for group in self.integration.load_groups(etags)]
        settings_endpoint.assert_not_called()
        members_endpoint.assert_not_called()
        assert len(new_read) == len(read_groups)
