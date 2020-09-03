from asyncio import ensure_future
from json import JSONDecodeError, loads
from typing import Dict

from aiohttp import web
from aiohttp.web import Request, Response

from ..controllers import SlackActionController
from ..models import SlackAction, blockkit


class SlackActionView(web.View):
    """
    This view handles requests + response from Slack's Actions API
    """

    def __init__(self, request: Request) -> None:
        super().__init__(request)

        # This class will be instantiated for each request. This means you must
        # bring singletons in scope from the request or app context like so.
        # These singletons are initialised in the main.py
        self._controller: SlackActionController = request.app["SlackActionController"]

    async def post(self) -> Response:
        try:
            p = await self.request.post()
            payload: Dict[str, any] = loads(p["payload"])
        except (JSONDecodeError, KeyError, ValueError):
            return web.Response(text="Could not parse body", status=400)

        # There can be more than one action, run them all
        for action in SlackAction.from_api(payload):

            # Actions from block kit sections
            if action.action_type == "block_actions":
                ensure_future(self._controller.route_action(action=action))

            # Actions from modal forms
            elif action.action_id == "ggroups_create_group":
                errors = await self._controller.validate_create_group_request(action)
                if errors:
                    return web.json_response(data={"response_action": "errors", "errors": errors})

                ensure_future(self._controller.send_create_group_request(action))

                blocks = [
                    blockkit.section(
                        blockkit.md(
                            "Sending request. You will receive a summary of the group info "
                            "when it is sent."
                        )
                    )
                ]
                return web.json_response(
                    data={
                        "response_action": "push",
                        "view": {
                            "type": "modal",
                            "callback_id": "ggroups_close_modal",
                            "title": blockkit.text(f"Create group"),
                            "close": blockkit.text(f"Create another"),
                            "submit": blockkit.text("Done"),
                            "blocks": blocks,
                        },
                    }
                )

            elif action.action_id == "ggroups_request_reason":
                # Deserialize the original action and route based on that.
                # Add reason to its value. Truncate it to 255 chars, just in case
                orig_action = self._controller.get_held_action(action.private_metadata)
                reason = self._controller.get_modal_value(action, "ggroups_request_reason")

                # You don't have to give a reason, but we do have to specify some value
                orig_action.value["reason"] = reason[: min(255, len(reason))] or None
                ensure_future(self._controller.route_action(orig_action))

            elif action.action_id == "ggroups_close_modal":
                return web.json_response(data={"response_action": "clear"})

            elif action.action_id in ["ggroups_add_members", "ggroups_remove_members"]:
                errors = self._controller.validate_modal_members(action)
                if errors:
                    return web.json_response(data={"response_action": "errors", "errors": errors})

                if action.action_id == "ggroups_add_members":
                    ensure_future(self._controller.send_add_members_request(action))
                    action = "Add"
                else:
                    ensure_future(self._controller.kick_members(action))
                    action = "Remove"

                blocks = [
                    blockkit.section(
                        blockkit.md(
                            "This may take a moment - you will"
                            " receive a message when it is complete."
                        )
                    )
                ]
                return web.json_response(
                    data={
                        "response_action": "push",
                        "view": {
                            "type": "modal",
                            "callback_id": "ggroups_close_modal",
                            "title": blockkit.text(f"{action} members"),
                            "close": blockkit.text("Back"),
                            "submit": blockkit.text("Done"),
                            "blocks": blocks,
                        },
                    }
                )

            else:
                print("Unhandled action type", action.action_type)

        return web.Response(status=204)
