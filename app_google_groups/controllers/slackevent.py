from datetime import datetime
from typing import List

from slack import WebClient

from ..models import GoogleGroup, LDAPUser, RequestAuditReport
from ..models.blockkit import actions, button, confirm, group_info, md, section, text
from ..models.request import DATE_FORMAT
from .ggroups import GoogleGroupsController
from .request import RequestController


class SlackEventController(object):
    def __init__(
        self, client: WebClient, ggroups: GoogleGroupsController, request: RequestController,
    ) -> None:
        # Variables starting with _ are protected. This means that they cannot be read
        # from outside this class
        self._client: WebClient = client
        self._ggroups: GoogleGroupsController = ggroups
        self._request: RequestController = request

    async def send_message(self, channel: str, msg: str) -> None:
        await self._client.chat_postMessage(channel=channel, text=msg, as_user=True)

    async def send_user_options(self, channel: str, user: LDAPUser, group: GoogleGroup) -> None:
        button_data = {
            "requester": user.aliases["slack"],
            "targets": [user.aliases["slack"]],
            "group_id": group.group_id,
        }
        member = self._ggroups.find_member(group, user.email)

        # Show join message depending on group membership
        buttons = []
        if not member:
            confirm_msg = "This will send a request" if group.protected else "Are you sure you want"
            buttons.append(
                button(
                    "ggroups_user_join",
                    "primary",
                    text("Join"),
                    button_data,
                    confirm(f"{confirm_msg} to join {group.email}"),
                )
            )

        if member or user.is_admin:
            buttons.append(button("ggroups_manage_group", "primary", text("Manage"), button_data))

        blocks = [
            group_info(group),
            actions(
                "ggroups_actions",
                *buttons,
                button("ggroups_show_members", "", text("View Members"), button_data,),
            ),
        ]

        await self._client.chat_postMessage(
            channel=channel, text="Here's your options", blocks=blocks,
        )

    async def send_invite_options(
        self, requester: LDAPUser, channel: str, users: List[LDAPUser], group: GoogleGroup
    ) -> None:
        button_data = {
            "requester": requester.aliases["slack"],
            "targets": [u.aliases["slack"] for u in users],
            "group_id": group.group_id,
            "new_request": True,
        }

        blocks = [
            section(md("Click this button to review your invite request.")),
            actions(
                "ggroups_actions",
                button("ggroups_manage_add_members", "primary", text("Add Members"), button_data,),
            ),
        ]

        await self._client.chat_postMessage(
            channel=channel, text="Open the manage group page to continue", blocks=blocks,
        )

    async def send_user_groups(self, channel: str, user: LDAPUser) -> None:
        groups = await self._ggroups.get_user_groups(email=user.email)

        # Max doesn't like empty sequences
        if not groups:
            await self._client.chat_postMessage(
                channel=channel,
                text="Not a member of any groups",
                blocks=[section(text(f"{user.name} is not currently a member of any groups"))],
            )
            return

        email_pad = max(len(g.email) for g in groups) + 2
        name_pad = max(len(g.name) for g in groups) + 2
        membertype_pad = max(len(self._ggroups.find_member(g, user.email).role) for g in groups) + 1

        # Some users have LOTS of groups, and if it's over 3kb then we need to split it across
        # multiple elements
        groups_lists = [""]
        for group in sorted(groups, key=lambda g: g.email):
            new_group = "{:{p}}{:{q}}{:{r}}\n".format(
                group.email,
                group.name,
                self._ggroups.find_member(group, user.email).role.lower(),
                p=email_pad,
                q=name_pad,
                r=membertype_pad,
            )

            # -9 for the backticks and newlines
            if len(groups_lists[-1]) + len(new_group) > 3000 - 9:
                groups_lists.append("")
            groups_lists[-1] += new_group

        blocks = [section(text(f"Here's the groups {user.name} is part of:"))] + [
            section(md(f"```\n{groups_list}```"), block_id=f"ggroups_user_groups_{i}")
            for i, groups_list in enumerate(groups_lists)
        ]

        await self._client.chat_postMessage(
            channel=channel, text="Here's your groups", blocks=blocks,
        )

    async def send_create_group_button(self, channel: str) -> None:
        blocks = [
            section(
                text("Click this button to get started creating a group :slightly_smiling_face:")
            ),
            actions(
                "ggroups_start_create_group_actions",
                button("ggroups_start_create_group", "primary", text("Create Group"), {},),
            ),
        ]

        await self._client.chat_postMessage(
            channel=channel, text="Click the button to get started", blocks=blocks,
        )

    async def send_usage(self, channel: str) -> None:
        blocks = [
            section(
                md(
                    "Hi there. I can help you self service your "
                    "Google Groups requests :slightly_smiling_face:\n\n"
                    "If you would like to see what groups you or a colleague are "
                    "in, send me their *slack handle*.\n\n"
                    "To manage your access to a group, "
                    "simply send me a *group's email address*."
                )
            ),
            section(md("To create a new Google Group, click this button:")),
            actions(
                "ggroups_start_create_group_actions",
                button("ggroups_start_create_group", "", text("Create Group"), {},),
            ),
        ]

        await self._client.chat_postMessage(
            channel=channel, text="How to use Google Groups App", blocks=blocks,
        )

    async def send_audit_report(
        self, channel: str, before: datetime, after: datetime, http_host: str
    ) -> None:
        reports: List[RequestAuditReport] = await self._request.generate_audit_reports(
            before.timestamp(), after.timestamp()
        )
        report_token: str = self._request.generate_token()

        row = "{:<9}|" + "{:<12}|" * len(reports) + "{:<5}"
        counts = [r.total for r in reports]
        approved = [r.total_approved for r in reports]
        denied = [r.total_denied for r in reports]
        no_admins = sum(r.acked_owner for r in reports)
        admins = sum(r.acked_admin for r in reports)
        rows = [
            f"Request stats by reason",
            row.format("Reason", *[r.action for r in reports], "Total"),
            row.format("=" * 9, *(["=" * 12] * len(reports)), "=" * 5),
            row.format("Approved", *approved, sum(approved)),
            row.format("Denied", *denied, sum(denied)),
            row.format("Total", *counts, sum(counts)),
            "",
            f"Requests without admin involvement: {no_admins}",
            f"Requests with admin involvement: {admins}",
        ]

        before_string = before.strftime(DATE_FORMAT)
        after_string = after.strftime(DATE_FORMAT)
        blocks = [
            section(md("```\n" + "\n".join(rows) + "\n```")),
            section(
                md(
                    f"<https://{http_host}/audit?token={report_token}&before={before_string}"
                    f"&after={after_string}|Download the raw data here.>"
                )
            ),
        ]

        await self._client.chat_postMessage(
            channel=channel, text="Audit report", blocks=blocks,
        )
