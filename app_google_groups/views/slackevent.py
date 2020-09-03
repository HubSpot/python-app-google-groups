import re
from asyncio import ensure_future
from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
from typing import Awaitable, Dict

from aiohttp import web
from aiohttp.web import Request, Response

from ..controllers import GoogleGroupsController, LDAPController, SlackEventController
from ..models import LDAPUser
from ..models.request import DATE_FORMAT, DEFAULT_AUDIT_RANGE


def ok() -> Response:
    return web.Response(status=204)


class SlackEventView(web.View):
    """
    This view handles requests + response from Slack's Events API
    """

    def __init__(self, request: Request) -> None:
        super().__init__(request)

        # This class will be instantiated for each request. This means you must
        # bring singletons in scope from the request or app context like so.
        # These singletons are initialised in the main.py
        config = request.app["ConfigSchema"]
        self._domain: str = config.domain
        self._app_id: str = config.slack.app_id
        self._controller: SlackEventController = request.app["SlackEventController"]
        self._ldap_controller: LDAPController = request.app["LDAPController"]
        self._ggroups_controller: GoogleGroupsController = request.app["GoogleGroupsController"]

        # Events are mostly plain text user-typed messages. Welcome to regex hell.
        self._regex_email_raw = (
            email_regex
        ) = r"(?:<mailto\:[^\|]+\|)?([\w\d\._%+-]+@[\w\d\._-]+)(?:\>)?"
        self._regex_user_raw = user_regex = r"<@([\w\d]+)>"

        regex_email = f"^{email_regex}$"
        regex_user = f"^{user_regex}$"
        regex_invite = f"^(?:invite\\s+)?(?:{user_regex}(?:,\\s)?)+\\s+(?:to\\s+)?{email_regex}$"
        regex_create_group = r"^.*create.*group.*$"
        regex_help = r"(?:hi|hello|hiya|hey|help)\s*$"
        regex_audit = r"^audit\s+report(?:\s+last\s+([0-9]+)\s+days?)?\s*$"
        regex_audit_range = (
            r"audit\s+report\s+from\s+((?:\d{2,4}\.?){3})\s+to\s+((?:\d{2,4}\.?){3})\s*$"
        )

        self._event_map: Dict[str, Awaitable[[], Response]] = {
            "url_verification": self.handle_verification,
            "app_rate_limited": self.handle_rate_limit,
            "event_callback": self.handle_event,
        }

        self._message_map: Dict[re.Pattern, Awaitable[[re.Match], None]] = {
            regex_email: self.send_group_info,
            regex_user: self.send_member_groups,
            regex_invite: self.send_invite_button,
            regex_create_group: self.send_create_group_button,
            regex_help: self.send_usage,
            regex_audit: self.send_audit,
            regex_audit_range: self.send_audit,
        }

        # Context for messages
        self.payload: Dict[str, any] = {}
        self.event: str = None
        self.user_id: str = None
        self.channel: str = None
        self.user: LDAPUser = None

    async def _check_email(self, email: str) -> bool:
        email_split = email.split("@")
        if email_split[1] != self._domain:
            await self._controller.send_message(
                self.channel,
                "Sorry, that email address doesn't look right."
                f" I'm expecting something@{self._domain}",
            )
            return False
        return True

    async def send_group_info(self, match: re.Match) -> None:
        # Verify the group email
        group_email = match[1].lower()
        if not await self._check_email(group_email):
            return

        group = await self._ggroups_controller.get_from_email(group_email)
        if group:
            await self._controller.send_user_options(
                channel=self.channel, user=self.user, group=group,
            )
        else:
            await self._controller.send_message(
                self.channel, "Sorry, I don't recognise that Google Group",
            )

    async def send_member_groups(self, match: re.Match) -> None:
        req_user = self._ldap_controller.get_user_from_slack(match[1].upper())
        if req_user:
            await self._controller.send_user_groups(channel=self.channel, user=req_user)
        else:
            await self._controller.send_message(self.channel, "Sorry, I don't recognise that user")

    async def send_invite_button(self, match: re.Match) -> None:
        user = self.user

        # Verify the group email
        group_email = match[match.lastindex].lower()
        if not await self._check_email(group_email):
            return

        group = await self._ggroups_controller.get_from_email(group_email)
        if not group:
            await self._controller.send_message(
                self.channel, "Sorry, I can't find that Google Group",
            )
            return

        # Get a list of the user ids
        # Unfortunately the invite_match regex cannot return > 1 slack ID
        user_ids = re.findall(self._regex_user_raw, self.event["text"])
        users = []
        for user_id in user_ids:
            req_user = self._ldap_controller.get_user_from_slack(user_id.upper())
            if not req_user:
                await self._controller.send_message(
                    self.channel, f"Sorry, I don't recognise <@{user_id.upper()}>"
                )
                return

            # TODO consider removing this
            elif self._ggroups_controller.find_member(group, req_user.email):
                await self._controller.send_message(
                    self.channel, f"<@{user_id.upper()}> is already a member of this group"
                )
                return

            users.append(req_user)

        # Button value size is limited
        if len(users) > 100:
            await self._controller.send_message(
                self.channel, "Sorry, you can only invite up to 100 users at a time"
            )

        elif (
            user.email == req_user.email
            or user.email in group.owners
            or user.is_admin
            or not group.protected
        ):
            await self._controller.send_invite_options(
                requester=user, channel=self.channel, users=users, group=group,
            )

        else:
            await self._controller.send_message(
                self.channel,
                "Sorry, you don't have permission to invite "
                "others to this group. Ask them to message me themselves.",
            )

    async def send_create_group_button(self, match: re.Match) -> None:
        await self._controller.send_create_group_button(self.channel)

    async def send_usage(self, match: re.Match) -> None:
        await self._controller.send_usage(self.channel)

    async def send_audit(self, match: re.Match) -> None:
        if not self.user.is_admin:
            await self._controller.send_message(
                self.channel, "Sorry, you must be an admin to generate audit reports.",
            )
            return

        # from X to Y
        if match.lastindex == 2:
            after = datetime.strptime(match[1], DATE_FORMAT)
            before = datetime.strptime(match[2], DATE_FORMAT)
            if after > before:
                before, after = after, before
            await self._controller.send_audit_report(self.channel, before, after, self.request.host)

        # last N days
        else:
            after_delta = match[1] and timedelta(days=int(match[1])) or DEFAULT_AUDIT_RANGE
            before = datetime.now(tz=timezone.utc)
            after = before - after_delta
            await self._controller.send_audit_report(self.channel, before, after, self.request.host)

    async def handle_message(self) -> None:
        # Definitely won't be None, checked in handle_event
        user_id: str = self.user_id
        channel: str = self.channel

        self.user = user = self._ldap_controller.get_user_from_slack(user_id)
        if not user:
            print("Could not find user", user_id, "in LDAP")
            await self._controller.send_message(
                channel,
                "Sorry, I couldn't figure out who you are. " "Please ask in <#CD7ARU0JW> for help",
            )
            return

        # Map the text to a handler
        text = self.event["text"]
        for pattern, handler in self._message_map.items():
            match = re.match(pattern, text, re.IGNORECASE)
            if match:
                await handler(match)
                return

        # Fallback, send help
        await self._controller.send_message(
            channel,
            "I don't recognise that command. If you would like some "
            "usage instructions, just send me 'help'",
        )
        return

    def handle_event(self) -> Response:
        self.event = event = self.payload["event"]

        # Prevent feedback loops
        if "bot_profile" in event and event["bot_profile"]["app_id"] == self._app_id:
            return ok()

        if event["type"] == "message" and event["channel_type"] in ["app_home", "im"]:
            self.user_id = self.event.get("user")
            self.channel = self.event.get("channel")

            # Check event has user and channel field
            if self.user_id and self.channel:
                ensure_future(self.handle_message())

        return ok()

    def unhandled_event(self) -> Response:
        print("Unhandled event type", self.payload.get("type", "Unspecified"))
        return web.Response(text="Unhandled event type", content_type="text/plain", status=400)

    def handle_verification(self) -> Response:
        return web.Response(text=self.payload["challenge"], content_type="text/plain")

    def handle_rate_limit(self) -> Response:
        # Give some info from the docs
        print("App rate limited by Slack! > 30,000 events in 60 minutes?")
        return ok()

    async def post(self) -> Response:
        try:
            self.payload = await self.request.json()
        except JSONDecodeError:
            return web.Response(text="Could not parse body", status=400)

        # Try resolve the handler based on the event type,
        # fall back to self.unhandled_event
        handler = self._event_map.get(self.payload.get("type", ""), self.unhandled_event)
        return handler()
