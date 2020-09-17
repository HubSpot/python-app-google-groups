import asyncio
import re
from datetime import datetime, timedelta, timezone
from json import dumps, loads
from typing import Dict, List, Optional
from uuid import uuid4

from aiohttp import ClientSession
from slack import WebClient
from slack.errors import SlackApiError
from slack.web.base_client import SlackResponse

from ..config import ConfigSchema
from ..models import Request, RequestMessage, ScheduleEvent, SlackAction
from ..models.blockkit import (
    actions,
    button,
    checkbox,
    checkboxes,
    confirm,
    context,
    find_block,
    group_info,
    inputbox,
    inputsection,
    md,
    remove_actions,
    remove_blocks,
    section,
    text,
    timestamp,
)
from .ggroups import GoogleGroupsController
from .ldap import LDAPController
from .request import RequestController
from .schedule import ScheduleController

PAGE_SIZE = 30

REQUEST_SENT = 1
MEMBERS_ADDED = 2
REASON_REQUESTED = 4


class SlackActionController(object):
    def __init__(
        self,
        client: WebClient,
        ldap: LDAPController,
        ggroups: GoogleGroupsController,
        schedule: ScheduleController,
        request: RequestController,
        config: ConfigSchema,
    ) -> None:
        # Variables starting with _ are protected. This means that they cannot be read
        # from outside this class
        self._client: WebClient = client
        self._ldap: LDAPController = ldap
        self._ggroups: GoogleGroupsController = ggroups
        self._schedule: ScheduleController = schedule
        self._request: RequestController = request
        self._approvals_channel: str = config.slack.approvals_channel
        self._domain: str = config.domain

        self._approval_timeout = timedelta(minutes=config.approval_timeout)
        self._regex_email = re.compile(
            r"^(?:<mailto\:[^\|]+\|)?([\w\d._%+-]+@{domain})(?:\>)?$".format(
                domain=config.domain.replace(".", r"\.")
            ),
            re.IGNORECASE,
        )
        self._action_map = {
            "ggroups_show_members": self.send_members_page,
            "ggroups_next": self.send_members_page,
            "ggroups_previous": self.send_members_page,
            "ggroups_user_join": self.send_join_request,
            "ggroups_user_become_owner": self.send_owner_request,
            "ggroups_user_leave": self.leave_group,
            "ggroups_join_approve": self.approve_join_group,
            "ggroups_join_deny": self.deny_request,
            "ggroups_become_owner_approve": self.approve_become_owner,
            "ggroups_become_owner_deny": self.deny_request,
            "ggroups_create_approve": self.approve_create_group,
            "ggroups_create_deny": self.deny_request,
            "ggroups_start_create_group": self.send_create_group_options,
            "ggroups_manage_group": self.send_manage_group_options,
            "ggroups_manage_add_members": self.send_add_members_options,
            "ggroups_manage_remove_members": self.send_remove_members_options,
        }

        # Short term hold for requests waiting for reason messages
        # Key is request_id
        self.held_actions: Dict[str, SlackAction] = {}

        # Register callbacks with the schedule controller
        self._schedule.register_callback("ggroups_approval_timeout", self.timeout_join_request)

    @staticmethod
    async def _respond(url: str, **kwargs: Dict[str, any]) -> None:
        async with ClientSession() as session:
            async with session.post(url, json=kwargs) as response:
                if response.status > 299:
                    print("Error sending action response:", await response.text())

    async def _update(self, msg: RequestMessage, **kwargs: Dict[str, any]) -> None:
        await self._client.chat_update(channel=msg.channel, ts=msg.ts, **kwargs)

    async def _inform_targets(
        self, action: SlackAction, request: Request, requester_id: str
    ) -> None:
        skipped = {
            requester_id,
            action.user_id,
        }
        targets = action.value.get("targets", [])
        if action.action_id == "ggroups_create_approve":
            targets = action.value.get("members", []) + action.value.get("owners", [])
        await asyncio.gather(
            *[
                self._client.chat_postMessage(
                    channel=target,
                    text=(f"You have been {request.heads_up_text}" f" by <@{requester_id}>"),
                )
                for target in targets
                if target not in skipped
            ]
        )

    async def _respond_to_approval(self, action: SlackAction, approved: bool) -> None:
        blocks = remove_blocks(
            blocks=action.message["blocks"], block_ids=["ggroups_approvals_choice"]
        )
        result = "approved" if approved else "denied"
        result_md = ":check: approved" if approved else ":denied-animated: denied"

        approver = self._ldap.get_user_from_slack(action.user_id)
        request: Request = await self._request.record_request_result(
            request_id=action.request_id, approver_email=approver.email, approved=approved,
        )
        blocks.append(
            context(
                md(
                    f"<@{action.user_id}> has {result_md} this request "
                    f"{timestamp(request.approval_timestamp)}"
                )
            )
        )

        # Update all owners and admins that got the request
        await asyncio.gather(
            *[
                self._update(
                    msg, blocks=blocks, text=f"Request {result}", as_user=True, parse="full"
                )
                for msg in request.messages
            ]
        )

        # Update the approver on the result
        await self._respond(
            action.response_url, blocks=blocks, text=f"Request {result}", replace_original=True
        )

        # Update the requester too
        requester = self._ldap.get_user_from_aliases(
            self._ggroups.get_user_emails(request.requester_email)
        )
        await self._client.chat_postMessage(
            channel=requester.aliases["slack"],
            text=f"Request {result}",
            blocks=[
                section(
                    md(
                        f"Your request to {request.recall_text} "
                        f"has been {result_md} by <@{action.user_id}>"
                    )
                )
            ],
            as_user=True,
        )

        if approved:
            await self._inform_targets(action, request, requester.aliases["slack"])

    async def _respond_already_approved(self, action: SlackAction, request: Request) -> None:
        result = "approved" if request.approved else "denied"
        approver = self._ldap.get_user_from_aliases(
            self._ggroups.get_user_emails(request.approver_email)
        )

        blocks = remove_blocks(
            blocks=action.message["blocks"], block_ids=["ggroups_approvals_choice"]
        )
        blocks.append(
            section(md(f"<@{approver.aliases['slack']}> has already {result} this request"))
        )
        await self._respond(
            action.response_url,
            blocks=blocks,
            text=f"Request already {result}",
            replace_original=True,
        )

    async def _respond_unauthorised(self, action: SlackAction) -> None:
        blocks = [section(text("Sorry, you are not authorised to do that"))]
        return await self._client.chat_postEphemeral(
            channel=action.channel_id,
            user=action.user_id,
            text="You are not authorised to do that",
            blocks=blocks,
            as_user=True,
        )

    async def _add_eyes(self, action: SlackAction) -> None:
        try:
            await self._client.reactions_add(
                name="eyes", channel=action.channel_id, timestamp=action.message["ts"]
            )
        except SlackApiError:
            # Ignore errors
            pass

    async def _remove_eyes(self, action: SlackAction) -> None:
        try:
            await self._client.reactions_remove(
                name="eyes", channel=action.channel_id, timestamp=action.message["ts"]
            )
        except SlackApiError:
            # Ignore errors
            pass

    def get_modal_value(self, action: SlackAction, key: str) -> any:
        for k, v in action.value.items():
            t = v["type"]
            # Checkboxes are heavily nested
            # Return true if the checkbox with the desired key is checked
            if t == "checkboxes":
                for cbox in v.get("selected_options", []):
                    if cbox["value"] == key:
                        return True

            elif k == key:
                if t == "multi_users_select":
                    return v.get("selected_users", [])
                if t == "plain_text_input":
                    # Slack used to return no value field,
                    # Then they updated it and it returns null instead
                    return v.get("value", "") or ""

    async def _try_load_data(self, action: SlackAction) -> None:
        if "requester" in action.value:
            action.user = self._ldap.get_user_from_slack(action.value["requester"])
        if "group_id" in action.value:
            action.group = await self._ggroups.get_from_id(action.value["group_id"])

    async def route_action(self, action: SlackAction) -> None:
        await self._try_load_data(action)
        # Find the handler for this action, or fall back to self.unhandled_event
        handler = self._action_map.get(action.action_id, self.unhandled_event)
        await handler(action=action)

    async def unhandled_event(self, action: SlackAction) -> None:
        print("Unhandled action", action.action_id)

    async def send_members_page(self, action: SlackAction) -> None:
        # This function is idempotent. Remove the message blocks that aren't needed
        # But preserve the original message
        blocks = remove_blocks(blocks=action.message["blocks"], block_ids=["ggroups_members"])
        actions = find_block(blocks=blocks, block_id="ggroups_actions")
        actions["elements"] = remove_actions(
            actions["elements"], ["ggroups_show_members", "ggroups_previous", "ggroups_next"]
        )

        # Just send back no members
        if not len(action.group.members):
            msg = "No members in this group"
            blocks.append(section(md(msg)))
            await self._respond(action.response_url, blocks=blocks, text=msg, replace_original=True)
            return

        # The buttons need to have the whole value so that user and group can be resolved
        index = action.value.get("index", 0)
        next_index = index + PAGE_SIZE
        previous_index = max(index - PAGE_SIZE, 0)
        if index != previous_index:
            action.value["index"] = previous_index
            actions["elements"].append(
                button("ggroups_previous", "", text("Previous"), dict(action.value))
            )

        if next_index < len(action.group.members):
            action.value["index"] = next_index
            actions["elements"].append(button("ggroups_next", "", text("Next"), dict(action.value)))

        group_members = sorted(action.group.members, key=lambda m: m.email)[
            index : index + PAGE_SIZE
        ]

        blocks.append(
            section(
                md("```\n" + "\n".join(member.email for member in group_members) + "```"),
                block_id="ggroups_members",
            )
        )

        await self._respond(
            action.response_url, blocks=blocks, text="Group members", replace_original=True
        )

    async def _send_join_request(
        self, action: SlackAction, channel: str, targets: List[str], event_id: int = 0,
    ) -> SlackResponse:
        """
        Sends the Slack approval requests to someone or somewhere
        """
        user, group = action.user, action.group
        button_data = {
            "request_id": action.request_id,
            "requester": user.aliases["slack"],
            "targets": targets,
            "group_id": group.group_id,
        }
        if event_id:
            button_data["event_id"] = event_id

        # Summary changes based on targets and user
        targets_tags = [f"<@{t}>" for t in targets]
        summary = f"{user.name} (<@{user.aliases['slack']}>) "
        if len(targets) > 1 or targets[0] != user.aliases["slack"]:
            summary += f"has requested to add {', '.join(targets_tags)} to {group.email}"
        else:
            summary += f"has requested to join {group.email}."

        if "reason" in action.value:
            summary += f"\nReason: `{action.value['reason'] or 'Not specified'}`"

        def confirm_message(action: str) -> str:
            return f"{', '.join(targets_tags)} will be *{action}* to {group.email}"

        blocks = [
            section(md(summary)),
            group_info(group),
            actions(
                "ggroups_approvals_choice",
                button(
                    "ggroups_join_approve",
                    "primary",
                    text("Approve"),
                    button_data,
                    confirm(confirm_message("added")),
                ),
                button(
                    "ggroups_join_deny",
                    "danger",
                    text("Deny"),
                    button_data,
                    confirm(confirm_message("denied")),
                ),
            ),
        ]

        return await self._client.chat_postMessage(
            channel=channel,
            text="Please review this Google Group join request",
            blocks=blocks,
            as_user=True,
        )

    async def _send_join_requests(self, action: SlackAction) -> int:
        slack_targets = action.value["targets"]

        # Ensure the payload contains the request id
        action.value["request_id"] = action.request_id

        # Load the target emails
        targets = [self._ldap.get_user_from_slack(slack_id).email for slack_id in slack_targets]

        # Check if the group is protected or user is an owner
        # Also check if someone other than the requester is being added
        member = self._ggroups.find_member(action.group, action.user.email)
        if (
            (not action.group.protected and slack_targets == [action.user_id])
            or (member and member.is_owner)
            or action.user.is_admin
        ):
            error = await self._ggroups.add_members(action.group, targets)
            if error:
                raise RuntimeError(error)

            await self._request.add_join_request(
                request_id=action.request_id,
                targets=targets,
                requester_email=action.user.email,
                group_email=action.group.email,
                messages=[],
            )
            request = await self._request.record_request_result(
                request_id=action.request_id, approver_email=action.user.email, approved=True
            )

            requester_id = self._ldap.get_user_from_email(request.requester_email).aliases["slack"]
            await self._inform_targets(action, request, requester_id)

            return MEMBERS_ADDED

        # Prompt a user to provide a reason for the request if they haven't already done so
        if "reason" not in action.value:
            await self.send_request_reason_prompt(action)
            return REASON_REQUESTED

        # If there are owners, create a timeout event to send the request to the
        # group create page
        owners = action.group.owners
        responses: List[SlackResponse] = []
        if owners:
            # Scheduled fail over to approvals channel if owners are there
            event: ScheduleEvent = await self._schedule.add_event(
                "ggroups_approval_timeout",
                (datetime.now(tz=timezone.utc) + self._approval_timeout).timestamp(),
                vars(action),
            )

            # Send to owners (top 3)
            for owner in owners[:3]:
                # It's hard to derive group owner slack IDs, since GoogleGroupMember
                # and LDAPUser are separate objects
                owner_ldap = self._ldap.get_user_from_aliases(
                    self._ggroups.get_user_emails(owner.email)
                )
                if not owner_ldap:
                    print(
                        "Failed to send Slack join request: Could not find LDAP user for owner",
                        owner.email,
                    )
                    continue
                responses.append(
                    await self._send_join_request(
                        action, owner_ldap.aliases["slack"], slack_targets, event.event_id
                    )
                )

        # No owners? Send to approvals channel
        else:
            responses.append(
                await self._send_join_request(action, self._approvals_channel, slack_targets)
            )

        await self._request.add_join_request(
            request_id=action.request_id,
            targets=targets,
            requester_email=action.user.email,
            group_email=action.group.email,
            messages=[RequestMessage.from_slack(slack_res) for slack_res in responses],
            reason=action.value["reason"],
        )

        return REQUEST_SENT

    async def _send_become_owner_request(
        self, action: SlackAction, channel: str, event_id: int = 0
    ) -> SlackResponse:
        user, group = action.user, action.group
        button_data = {
            "request_id": action.request_id,
            "requester": user.aliases["slack"],
            "group_id": group.group_id,
        }
        if event_id:
            button_data["event_id"] = event_id
        summary = (
            f"{user.name} (<@{user.aliases['slack']}>) has requested to "
            f"become an owner of {group.email}."
        )
        confirm_message = (
            lambda action: f"{user.name} (<@{user.aliases['slack']}>) "
            f"will be *{action}* as an owner of {group.email}"
        )

        blocks = [
            section(md(summary)),
            group_info(group),
            actions(
                "ggroups_approvals_choice",
                button(
                    "ggroups_become_owner_approve",
                    "primary",
                    text("Approve"),
                    button_data,
                    confirm(confirm_message("added")),
                ),
                button(
                    "ggroups_become_owner_deny",
                    "danger",
                    text("Deny"),
                    button_data,
                    confirm(confirm_message("denied")),
                ),
            ),
        ]

        return await self._client.chat_postMessage(
            channel=channel,
            text="Please review this Google Group become owner request",
            blocks=blocks,
            as_user=True,
        )

    def get_held_action(self, request_id: str) -> Optional[SlackAction]:
        if request_id in self.held_actions:
            action = self.held_actions[request_id]
            del self.held_actions[request_id]
            return action

    async def send_request_reason_prompt(self, action: SlackAction) -> None:
        self.held_actions[action.request_id] = action

        blocks = [
            section(
                text(
                    "Please provide a reason for your request, to help our admins action it sooner."
                )
            ),
            inputsection(
                text("Reason"),
                inputbox("ggroups_request_reason", "plain_text_input", text("I like doggos")),
            ),
        ]

        return await self._client.views_open(
            trigger_id=action.trigger_id,
            view={
                "type": "modal",
                "callback_id": "ggroups_request_reason",
                "title": text("Request Reason"),
                "submit": text("Submit"),
                "close": text("Cancel"),
                "blocks": blocks,
                "private_metadata": action.request_id,
            },
        )

    async def send_join_request(self, action: SlackAction) -> None:
        blocks = remove_blocks(
            blocks=action.message["blocks"], block_ids=["ggroups_members", "ggroups_actions"]
        )

        try:
            result = await self._send_join_requests(action)
        except RuntimeError as error:
            print("Error adding user(s) to", action.group.email, ":", error)
            blocks.append(section(md(f"Error joining group: {error}")))
            await self._respond(action.response_url, blocks=blocks, text="Error joining group")
            return

        msg = ""
        targets = action.value["targets"]
        if result == REQUEST_SENT:
            msg = f":email: Request sent to join {action.group.name} ({action.group.email})!"
        elif result == MEMBERS_ADDED:
            if targets[0] == action.user_id and len(targets) == 1:
                msg = (
                    ":heavy_check_mark: You have joined "
                    f"{action.group.name} ({action.group.email})!"
                )
            else:
                users = ", ".join(f"<@{t}>" for t in targets[: min(len(targets), 3)])
                remainder = len(targets) - 3
                msg = (
                    f":heavy_check_mark: You have added {users} "
                    f"{f'and {remainder} others ' if remainder > 0 else ''}"
                    f"to {action.group.name} ({action.group.email})!"
                )

        if msg:
            blocks.append(section(md(msg)))
            await self._respond(action.response_url, blocks=blocks, text=msg)

    async def timeout_join_request(self, event: ScheduleEvent) -> None:
        action: SlackAction = SlackAction.from_dict(event.payload)
        if "targets" not in action.value:
            group = action.group.email if action.group else "UNKNOWN GROUP"
            user = action.user.name if action.user else "UNKNOWN USER"
            print(f"Request to add NO ONE to {group} by {user} is being dropped")
            return
        targets_pretty: str = ", ".join(action.value["targets"])
        print(
            f"Request to owners to add {targets_pretty} to {action.group.email} timed out. "
            "Sending to channel"
        )
        message = await self._send_join_request(
            action, self._approvals_channel, action.value["targets"]
        )
        await self._request.add_messages(
            RequestMessage.from_slack(message), request_id=action.request_id
        )

    async def send_owner_request(self, action: SlackAction) -> None:
        # Ensure the payload contains the request id
        action.value["request_id"] = action.request_id

        blocks = [
            section(
                md(
                    ":heavy_check_mark: Sending request. You will receive a confirmation"
                    " when it is sent and when it has been approved or denied."
                )
            )
        ]

        # If user closes the view fast this will fail
        try:
            await self._client.views_update(
                trigger_id=action.trigger_id,
                view_id=action.channel_id,
                view={
                    "type": "modal",
                    "callback_id": "ggroups_close_modal",
                    "title": text("Become owner of group"),
                    "close": text("Back"),
                    "submit": text("Done"),
                    "blocks": blocks,
                },
            )
        except SlackApiError:
            pass

        # If there are owners, create a timeout event to send the request to the
        # group create page
        owners = action.group.owners
        responses: List[SlackResponse] = []
        if owners:
            # Scheduled fail over to approvals channel if owners are there
            event: ScheduleEvent = await self._schedule.add_event(
                "ggroups_approval_timeout",
                (datetime.now(tz=timezone.utc) + self._approval_timeout).timestamp(),
                vars(action),
            )

            # Send to owners
            owner_slacks: List[str] = []
            for owner in owners:
                # It's hard to derive group owner slack IDs, since GoogleGroupMember
                # and LDAPUser are separate objects
                owner_ldap = self._ldap.get_user_from_aliases(
                    self._ggroups.get_user_emails(owner.email)
                )
                if not owner_ldap:
                    print(
                        "Failed to send become owner request: "
                        "Could not find LDAP user for existing owner",
                        owner.email,
                    )
                else:
                    owner_slacks.append(owner_ldap.aliases["slack"])

            responses.extend(
                await asyncio.gather(
                    *[
                        self._send_become_owner_request(action, slack_id, event.event_id)
                        for slack_id in owner_slacks
                    ]
                )
            )

        # No owners? Send to approvals channel
        else:
            responses.append(await self._send_become_owner_request(action, self._approvals_channel))

        await self._request.add_become_owner_request(
            request_id=action.request_id,
            requester_email=action.user.email,
            group_email=action.group.email,
            messages=[RequestMessage.from_slack(slack_res) for slack_res in responses],
        )

        await self._client.chat_postMessage(
            channel=action.user.aliases["slack"],
            text="Request sent!",
            blocks=[
                section(md(f":email: Request sent to become an owner of {action.group.email}!"))
            ],
            as_user=True,
        )

    async def leave_group(self, action: SlackAction) -> None:
        member = self._ggroups.find_member(action.group, action.user.email)

        await self._client.views_push(
            trigger_id=action.trigger_id,
            view={
                "type": "modal",
                "callback_id": "ggroups_close_modal",
                "title": text("Leaving group"),
                "close": text("Back"),
                "submit": text("Done"),
                "blocks": [
                    section(
                        md(
                            "Removing you from the group. This takes a second, "
                            "you will receive a message when it is complete."
                        )
                    )
                ],
            },
        )

        msg = f"You have left { action.group.name }"
        try:
            await self._ggroups.remove_member(action.group, member)
            await self._request.add_leave_request(
                request_id=action.request_id,
                requester_email=member.email,
                group_email=action.group.email,
            )

        except ValueError as error:
            print("Error removing user", member.email, "from", action.group.email, ":", error)
            msg = f"Error removing user from group: {error}"

        await self._client.chat_postMessage(
            channel=action.user.aliases["slack"], text=msg,
        )

    async def approve_join_group(self, action: SlackAction) -> None:
        # Check user permissions
        approver = self._ldap.get_user_from_slack(action.user_id)
        if (
            not action.group or approver.email not in action.group.owners
        ) and not approver.is_admin:
            await self._respond_unauthorised(action)
            return

        # Check existing approval
        request = await self._request.get_request(action.request_id)
        if request.approved is not None:
            await self._respond_already_approved(action, request)
            return

        # Add eyes as a reaction so people know it's being run
        await self._add_eyes(action)

        # Load the target users
        targets = [self._ldap.get_user_from_slack(slack_id) for slack_id in action.value["targets"]]

        error = await self._ggroups.add_members(action.group, [t.email for t in targets])

        if error:
            print("Error adding user(s) to", action.group.email, ":", error)
            msg = f"Error adding user(s) to group: {error}"
            await self._respond(action.response_url, text=msg)

        else:
            await self._respond_to_approval(action, True)
            if "event_id" in action.value:
                await self._schedule.cancel_event(action.value["event_id"])

        await self._remove_eyes(action)

    async def deny_request(self, action: SlackAction) -> None:
        # Check user permissions
        requester = self._ldap.get_user_from_slack(action.user_id)
        if (action.group and requester.email not in action.group.owners) and not requester.is_admin:
            await self._respond_unauthorised(action)
            return

        # Check existing approval
        request = await self._request.get_request(action.request_id)
        if request.approved is not None:
            await self._respond_already_approved(action, request)
            return

        if "event_id" in action.value:
            await self._schedule.cancel_event(action.value["event_id"])

        await self._respond_to_approval(action, False)

    async def approve_become_owner(self, action: SlackAction) -> None:
        # Check user permissions
        approver = self._ldap.get_user_from_slack(action.user_id)
        if not approver.is_admin:
            await self._respond_unauthorised(action)
            return

        # Check existing approval
        request = await self._request.get_request(action.request_id)
        if request.approved is not None:
            await self._respond_already_approved(action, request)
            return

        # Add eyes as a reaction so people know it's being run
        await self._add_eyes(action)

        member = self._ggroups.find_member(action.group, action.user.email)

        if member in action.group.owners:
            print(
                "Attempted double-approval of become owner ",
                action.user.email,
                "to",
                action.group.email,
            )
            msg = f"This request has already been approved"
            await self._respond(action.response_url, text=msg)

        error = await self._ggroups.change_role(action.group, member, "OWNER")

        if error:
            print("Error adding owner", action.user.email, "to", action.group.email, ":", error)
            msg = f"Error adding owner to group: {error}"
            await self._respond(action.response_url, text=msg)

        else:
            await self._respond_to_approval(action, True)
            if "event_id" in action.value:
                await self._schedule.cancel_event(action.value["event_id"])

        await self._remove_eyes(action)

    async def approve_create_group(self, action: SlackAction) -> None:
        # Check user permissions
        approver = self._ldap.get_user_from_slack(action.user_id)
        if not approver.is_admin:
            await self._respond_unauthorised(action)
            return

        # Check existing approval
        request = await self._request.get_request(action.request_id)
        if request.approved is not None:
            await self._respond_already_approved(action, request)
            return

        # Add eyes as a reaction so people know it's being run
        await self._add_eyes(action)

        name = action.value["name"]
        email = action.value["email"]
        description = action.value["description"]
        protect = action.value["protect"]

        # Check if the group already exists. If the request crashed we don't want to duplicate it
        group = await self._ggroups.get_from_email(email)
        if not group:
            group = await self._ggroups.create(
                email=email, name=name, description=description, protect=protect
            )

        owners = [self._ldap.get_user_from_slack(user_id) for user_id in action.value["owners"]]
        error = await self._ggroups.add_members(
            group=group, emails=[u.email for u in owners], role="OWNER"
        )
        if error:
            msg = f"Failed to add some owners to the new group {group.name}: " + error
            print(msg)
            await self._client.chat_postMessage(channel=self._approvals_channel, text=msg)

        members = [self._ldap.get_user_from_slack(user_id) for user_id in action.value["members"]]
        error = await self._ggroups.add_members(group=group, emails=[u.email for u in members])
        if error:
            msg = f"Failed to add some members to the new group {group.name}: " + error
            print(msg)
            await self._client.chat_postMessage(channel=self._approvals_channel, text=msg)

        await self._respond_to_approval(action, True)
        await self._remove_eyes(action)

    async def send_create_group_options(self, action: SlackAction) -> None:
        protected_checkbox = checkbox(
            "ggroups_group_protect", text("Protect this group (new members must request to join)"),
        )

        blocks = [
            inputsection(
                text("Email Address"),
                inputbox(
                    "ggroups_group_email",
                    "plain_text_input",
                    text(f"emea-daily-doggos@{self._domain}"),
                ),
                hint=text(f"What email address should it have? (Make sure it is @{self._domain})"),
            ),
            section(text("Please note this will submit an approval request to admins.")),
            inputsection(
                text("Name"),
                inputbox("ggroups_group_name", "plain_text_input", text("EMEA Daily Doggos")),
                hint=text(
                    "What do you want to call this group? (It should be close to the email address)"
                ),
            ),
            inputsection(
                text("Description"),
                inputbox(
                    "ggroups_group_description",
                    "plain_text_input",
                    text(f"Good boys, daily (EMEA region)"),
                ),
                hint=text(f"What email address should it have? (Make sure it is @{self._domain})"),
            ),
            inputsection(
                text("Options"),
                checkboxes("ggroups_group_checkboxes", [protected_checkbox]),
                optional=True,
            ),
            inputsection(
                text("Owners"),
                inputbox(
                    "ggroups_group_owners",
                    "multi_users_select",
                    text("@jdoe"),
                    initial_users=[action.user_id],
                    max_selected_items=10,
                ),
                hint=text("Who can approve new members in this group?"),
            ),
            inputsection(
                text("Members"),
                inputbox(
                    "ggroups_group_members",
                    "multi_users_select",
                    text("@jdoe"),
                    # I did some napkin math to work out how many slackIDs we could fit in
                    # the 2000 characters for action data
                    max_selected_items=100,
                ),
                hint=text(
                    "Who will initially be in this group?"
                    "(You can add more later. Don't include owners)"
                ),
                optional=True,
            ),
        ]

        return await self._client.views_open(
            trigger_id=action.trigger_id,
            view={
                "type": "modal",
                "callback_id": "ggroups_create_group",
                "title": text("Create a Group"),
                "submit": text("Submit"),
                "close": text("Cancel"),
                "blocks": blocks,
            },
        )

    async def validate_create_group_request(self, action: SlackAction) -> Dict[str, str]:
        email = self.get_modal_value(action, "ggroups_group_email")
        members = self.get_modal_value(action, "ggroups_group_members")
        owners = self.get_modal_value(action, "ggroups_group_owners")

        # Valdiate the request. Only some things need to be checked, as Slack will make sure:
        # - Owners must be > 1, < 10
        # - Members must be < 100
        # - Name and email can't be empty
        # - All owners and members are valid slackIDs

        # Append domain if user didn't put it in
        if f"@{self._domain}" not in email:
            email += f"@{self._domain}"

        errors = {}
        if "+" in email:
            errors[
                action.value["ggroups_group_email"]["parent"]
            ] = "The email address cannot contain an alias part (+blah)"
            return errors

        if self._domain != email.split("@")[1]:
            errors[
                action.value["ggroups_group_email"]["parent"]
            ] = f"The email address must be @{self._domain}"
            return errors

        match = self._regex_email.match(email)
        if not match:
            errors[
                action.value["ggroups_group_email"]["parent"]
            ] = "This email address is not valid"
            return errors
        email = match.group(1)

        # Check if group name is taken
        if await self._ggroups.get_from_email(email):
            errors[
                action.value["ggroups_group_email"]["parent"]
            ] = "This email address is already taken"

        for member in members:
            if member in owners:
                errors[
                    action.value["ggroups_group_members"]["parent"]
                ] = f"Users cannot be an owner and a regular member"

        for users, field in [(members, "ggroups_group_members"), (owners, "ggroups_group_owners")]:
            for user in users:
                if not self._ldap.get_user_from_slack(user):
                    errors[action.value[field]["parent"]] = (
                        f"Cannot find user with Slack ID {user}."
                        " Please check the list, then contact an admin."
                    )

        return errors

    async def send_create_group_request(self, action: SlackAction) -> None:
        name = self.get_modal_value(action, "ggroups_group_name")
        email = self.get_modal_value(action, "ggroups_group_email")
        description = self.get_modal_value(action, "ggroups_group_description")
        members = self.get_modal_value(action, "ggroups_group_members")
        owners = self.get_modal_value(action, "ggroups_group_owners")
        protect = self.get_modal_value(action, "ggroups_group_protect")
        request_id = uuid4().hex

        # Append domain if user didn't put it in
        if f"@{self._domain}" not in email:
            email += f"@{self._domain}"

        button_data = {
            "request_id": request_id,
            "name": name,
            "email": email,
            "description": description,
            "members": members,
            "owners": owners,
            "protect": protect,
        }

        group_owners = "• " + ("\n• ".join(f"<@{owner}>" for owner in owners) or "None")
        confirm_message = (
            lambda action: f"Request to create group '{name}' ({email}) will be *{action}*"
        )

        info = section(
            fields=[
                md(f"*Name:*\n{name}"),
                md(f"*Email:*\n{email}"),
                md(f"*Members:*\n{len(members + owners)}"),
                md(f"*Description:*\n{description or 'None'}"),
                md(f"*Owners:*\n{group_owners}"),
                md(f"*Protected:*\n{protect}"),
            ]
        )

        blocks = [
            section(md(f"<@{action.user_id}> has requested to create a new group '{name}'.")),
            info,
            actions(
                "ggroups_approvals_choice",
                button(
                    "ggroups_create_approve",
                    "primary",
                    text("Approve"),
                    button_data,
                    confirm(confirm_message("approved")),
                ),
                button(
                    "ggroups_create_deny",
                    "danger",
                    text("Deny"),
                    button_data,
                    confirm(confirm_message("denied")),
                ),
            ),
        ]

        blocks_result = [
            section(md(f":email: Request sent! Here's the details:")),
            info,
        ]

        slack_res: SlackResponse = await self._client.chat_postMessage(
            channel=self._approvals_channel,
            text="Please review this Google Group creation request",
            blocks=blocks,
            as_user=True,
        )

        message = RequestMessage.from_slack(slack_res)
        creator = self._ldap.get_user_from_slack(action.user_id)

        await self._request.add_create_request(
            request_id=request_id,
            requester_email=creator.email,
            group_email=email,
            messages=[message],
        )

        await self._client.chat_postMessage(
            channel=action.user_id, text="Request sent!", blocks=blocks_result, as_user=True,
        )

    async def send_manage_group_options(self, action: SlackAction) -> None:
        button_data = {
            "requester": action.user.aliases["slack"],
            "group_id": action.group.group_id,
        }
        confirm_msg = "and will have to request access to rejoin" if action.group.protected else ""
        member = self._ggroups.find_member(action.group, action.user.email)
        is_owner = member and member.is_owner
        is_admin = action.user.is_admin

        blocks = [
            section(text(f"Managing {action.group.name}")),
        ]

        if member:
            blocks.append(
                section(
                    md(":wave: *Leave Group*\nStop receiving mail from this group."),
                    accessory=button(
                        "ggroups_user_leave",
                        "danger",
                        text("Leave"),
                        button_data,
                        confirm(f"You will be removed from {action.group.name} {confirm_msg}"),
                    ),
                )
            )
        if member and not is_owner:
            blocks.append(
                section(
                    md(
                        ":briefcase: *Become an Owner*\nAdd and remove members yourself,"
                        " and manage requests to join this group."
                    ),
                    accessory=button(
                        "ggroups_user_become_owner",
                        "primary",
                        text("Become an Owner"),
                        button_data,
                        confirm(
                            f"This will request that you become an owner of {action.group.name}"
                        ),
                    ),
                )
            )
        if is_owner or is_admin or not action.group.protected:
            blocks.append(
                section(
                    md(":incoming_envelope: *Add Members*\nAdd people to this group."),
                    accessory=button(
                        "ggroups_manage_add_members", "primary", text("Add Members"), button_data,
                    ),
                )
            )
        if is_owner or is_admin:
            blocks.append(
                section(
                    md(":x: *Remove Members*\nRemove people from this group."),
                    accessory=button(
                        "ggroups_manage_remove_members",
                        "danger",
                        text("Remove Members"),
                        button_data,
                    ),
                )
            )

        return await self._client.views_open(
            trigger_id=action.trigger_id,
            view={
                "type": "modal",
                "callback_id": "ggroups_manage_group",
                "title": text("Manage group"),
                "close": text("Close"),
                "blocks": blocks,
            },
        )

    async def send_add_members_options(self, action: SlackAction) -> None:
        blocks = [
            section(
                text(
                    "Please note that the available users includes"
                    " members and non members of the group."
                )
            ),
            inputsection(
                text("New Members"),
                inputbox(
                    "ggroups_group_members",
                    "multi_users_select",
                    text("@jdoe"),
                    # I did some napkin math to work out how many slackIDs we could fit in
                    # the 2000 characters for action data
                    max_selected_items=100,
                    initial_users=action.value.get("targets", []),
                ),
                hint=text(f"List members you would like to add to this group"),
                block_id="ggroups_members_input",
            ),
        ]

        member = self._ggroups.find_member(action.group, action.user.email)
        if not action.user.is_admin and not (member and member.is_owner):
            blocks.append(
                inputsection(
                    text("Reason"),
                    inputbox(
                        "ggroups_request_reason", "plain_text_input", text("These people are cool"),
                    ),
                    hint=text(
                        "Please provide a reason for your request,"
                        " to help our admins action it sooner."
                    ),
                )
            )

        view = {
            "type": "modal",
            "callback_id": "ggroups_add_members",
            "title": text(f"Manage members"),
            "submit": text("Submit"),
            "close": text("Cancel"),
            "blocks": blocks,
            "private_metadata": dumps(action.value),
        }

        if action.value.get("new_request", False):
            return await self._client.views_open(trigger_id=action.trigger_id, view=view,)
        return await self._client.views_push(trigger_id=action.trigger_id, view=view,)

    async def send_remove_members_options(self, action: SlackAction) -> None:
        blocks = [
            section(
                text(
                    "Please note that the available users includes"
                    " members and non members of the group."
                )
            ),
            inputsection(
                text("Members"),
                inputbox(
                    "ggroups_group_members",
                    "multi_users_select",
                    text("@jdoe"),
                    # I did some napkin math to work out how many slackIDs we could fit in
                    # the 2000 characters for action data
                    max_selected_items=100,
                ),
                hint=text(f"List members you would like to remove from this group"),
                block_id="ggroups_members_input",
            ),
        ]

        return await self._client.views_push(
            trigger_id=action.trigger_id,
            view={
                "type": "modal",
                "callback_id": "ggroups_remove_members",
                "title": text(f"Manage members"),
                "submit": text("Submit"),
                "close": text("Cancel"),
                "blocks": blocks,
                "private_metadata": dumps(action.value),
            },
        )

    def validate_modal_members(self, action: SlackAction) -> Dict[str, str]:
        errors = {}
        targets = self.get_modal_value(action, "ggroups_group_members")

        # Valdiate the request. Only some things need to be checked, as Slack will make sure:
        # - Targets must be < 100
        # - Name and email can't be empty
        # - All owners and targets are valid slackIDs
        for target in targets:
            if not self._ldap.get_user_from_slack(target):
                errors["ggroups_members_input"] = f"Cannot find user with Slack ID {target}."

        return errors

    async def send_add_members_request(self, action: SlackAction) -> None:
        reason = self.get_modal_value(action, "ggroups_request_reason")
        slack_targets = self.get_modal_value(action, "ggroups_group_members")

        # Hax, because usually the value contains this info
        action.value = loads(action.private_metadata)
        action.value["targets"] = slack_targets
        action.value["reason"] = reason
        await self._try_load_data(action)

        try:
            result = await self._send_join_requests(action)
        except RuntimeError as error:
            print("Error adding user(s) to", action.group.email, ":", error)
            msg = f"Error adding user(s) to group: {error}"
            await self._client.chat_postMessage(
                channel=action.user_id, text=msg, as_user=True,
            )
            return

        msg = ":email: Request sent to add "
        if result == MEMBERS_ADDED:
            msg = ":heavy_check_mark: You have added "

        users = ", ".join(f"<@{t}>" for t in slack_targets[: min(len(slack_targets), 3)])
        remainder = len(slack_targets) - 3
        msg += (
            f"{users} {f'and {remainder} others ' if remainder > 0 else ''}"
            f"to {action.group.name} ({action.group.email})!"
        )

        await self._client.chat_postMessage(
            channel=action.user_id, text=msg, blocks=[section(md(msg))], as_user=True,
        )

    async def kick_members(self, action: SlackAction) -> None:
        slack_targets = self.get_modal_value(action, "ggroups_group_members")
        action.value = loads(action.private_metadata)
        await self._try_load_data(action)

        # Load the target emails
        targets = [self._ldap.get_user_from_slack(slack_id).email for slack_id in slack_targets]

        # Load the target members
        members = [self._ggroups.find_member(action.group, email) for email in targets]

        # Add the request now, incase any one of the remove requests fails fatally
        # We don't want to lose the trail on partial deletes
        request = await self._request.add_leave_request(
            request_id=action.request_id,
            requester_email=action.user.email,
            group_email=action.group.email,
            targets=targets,
        )

        users = ", ".join(f"<@{t}>" for t in slack_targets[: min(len(slack_targets), 3)])
        remainder = len(slack_targets) - 3
        msg = (
            f"Removed {users} {f'and {remainder} others ' if remainder > 0 else ''}"
            f"from {action.group.name} ({action.group.email})!"
        )

        for member in members:
            if member is None:
                continue

            try:
                await self._ggroups.remove_member(action.group, member)
            except Exception as error:
                msg = f"Error removing member {member.email} from {action.group.email}: {error}"
                print(msg)
                break

        await self._client.chat_postMessage(
            channel=action.user_id, text=msg, as_user=True,
        )

        requester_id = action.user.aliases["slack"]
        await self._inform_targets(action, request, requester_id)
