from json import loads
from typing import Dict, List, Optional
from uuid import uuid4

from .ggroup import GoogleGroup
from .ldapuser import LDAPUser


class SlackAction(object):
    def __init__(
        self,
        action_id: str,
        action_type: str,
        channel_id: str,
        user_id: str,
        value: Dict[str, any],
        message: Dict[str, any],
        trigger_id: Optional[str] = None,
        response_url: Optional[str] = None,
        private_metadata: Optional[str] = None,
    ) -> None:
        self.action_id = action_id
        self.action_type = action_type
        self.channel_id = channel_id
        self.user_id = user_id
        self.value = value
        self.message = message
        self.trigger_id = trigger_id
        self.response_url = response_url
        self.private_metadata = private_metadata
        self.user: Optional[LDAPUser] = None
        self.group: Optional[GoogleGroup] = None

    @property
    def __dict__(self) -> Dict[str, any]:
        data = {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "channel_id": self.channel_id,
            "user_id": self.user_id,
            "value": self.value,
            "message": self.message,
            "trigger_id": self.trigger_id,
            "response_url": self.response_url,
            "user": vars(self.user),
            "group": vars(self.group),
        }
        if self.user:
            data["user"] = vars(self.user)
        if self.group:
            data["group"] = vars(self.group)
        return data

    @classmethod
    def from_api(cls, payload: Dict[str, any]) -> List["SlackAction"]:
        if payload["type"] == "view_submission":
            view = payload["view"]
            urls = payload.get("response_urls", [])
            vid = view["id"]
            del view["id"]
            return [
                cls(
                    action_id=view["callback_id"],
                    action_type=payload["type"],
                    channel_id=vid,
                    user_id=payload["user"]["id"],
                    # Flatten the first level of value, it has untagged block ids
                    # Parent block ID is needed for form validation
                    value={
                        k: {**v, "parent": parent}
                        for parent, field in view["state"]["values"].items()
                        for k, v in field.items()
                    },
                    message=view,
                    trigger_id=payload.get("trigger_id", None),
                    response_url=urls.pop() if urls else None,
                    private_metadata=view.get("private_metadata", None),
                )
            ]

        return [
            cls(
                action_id=action["action_id"],
                action_type=payload["type"],
                channel_id=payload.get("channel", payload.get("view", None))["id"],
                user_id=payload["user"]["id"],
                value=loads(action["value"]),
                message=payload.get("message", {}),
                trigger_id=payload.get("trigger_id", None),
                response_url=payload.get("response_url", None),
            )
            for action in payload["actions"]
        ]

    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> "SlackAction":
        instance = cls(
            action_id=data["action_id"],
            action_type=data["action_type"],
            channel_id=data["channel_id"],
            user_id=data["user_id"],
            value=data["value"],
            message=data["message"],
            trigger_id=data["trigger_id"],
            response_url=data["response_url"],
        )
        if isinstance(data.get("user"), dict):
            instance.user = LDAPUser.from_dict(data["user"])
        if isinstance(data.get("group"), dict):
            instance.group = GoogleGroup.from_dict(data["group"])
        return instance

    @property
    def request_id(self) -> str:
        # Why not use the dict.setdefault() method? Well that would mean evaluating
        # uuid4().hex even if self.data has a request_id field
        if "request_id" not in self.value:
            # Write it into self.value so that it is stored in serialisation
            self.value["request_id"] = uuid4().hex
        return self.value["request_id"]
