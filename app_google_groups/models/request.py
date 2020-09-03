from datetime import timedelta
from json import dumps
from typing import Dict, List, Optional

DEFAULT_AUDIT_RANGE = timedelta(days=90)

# Date format for query string params
DATE_FORMAT = "%d.%m.%Y"


class RequestAction(str):
    pass


class RequestActions(object):
    CreateGroup = RequestAction("create_group")
    JoinGroup = RequestAction("join_group")
    LeaveGroup = RequestAction("leave_group")
    BecomeGroupOwner = RequestAction("become_owner")


class RequestMessage(object):
    def __init__(self, channel: str, ts: str) -> None:
        self.channel: str = channel
        self.ts: str = ts

    def __eq__(self, other: any) -> bool:
        if isinstance(other, RequestMessage):
            return self.channel == other.channel and self.ts == other.ts
        return False

    @classmethod
    def from_slack(cls, slack_data: Dict[str, any]) -> "RequestMessage":
        return cls(channel=slack_data.data["channel"], ts=slack_data.data["ts"])

    @classmethod
    def from_db(cls, db_data: Dict[str, any]) -> "RequestMessage":
        return cls(**db_data)


class RequestAuditReport(object):
    def __init__(self, action: str,) -> None:
        self.action = action
        self.total = 0
        self.total_approved = 0
        self.total_denied = 0
        self.total_escalated = 0
        self.total_unescalated = 0

        self.ack_time_admin: int = 0
        self.acked_admin: int = 0
        self.ack_time_owner: int = 0
        self.acked_owner: int = 0

    def __iadd__(self, other: any) -> "RequestAuditReport":
        if isinstance(other, RequestAuditReport):
            self.total += other.total
            self.total_approved += other.total_approved
            self.total_denied += other.total_denied
            self.total_escalated += other.total_escalated
            self.total_unescalated += other.total_unescalated
            self.ack_time_admin += other.ack_time_admin
            self.acked_admin += other.acked_admin
            self.ack_time_owner += other.ack_time_owner
            self.acked_owner += other.acked_owner
        return self

    def _pretty_time(self, time: int) -> str:
        hours, remainder = divmod(time, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours}h {minutes}m"

    @property
    def ack_time_admin_pretty(self) -> str:
        return self._pretty_time(self.ack_time_admin)

    @property
    def ack_time_owner_pretty(self) -> str:
        return self._pretty_time(self.ack_time_owner)

    def add_ack_time(self, ack_time: int, is_admin: bool = False) -> None:
        if is_admin:
            self.acked_admin += 1
            self.ack_time_admin = (ack_time - self.ack_time_admin) / self.acked_admin
        else:
            self.acked_owner += 1
            self.ack_time_owner = (ack_time - self.ack_time_owner) / self.acked_owner


class Request(object):
    table_name = "ggroups_requests"

    def __init__(
        self,
        request_id: str,
        timestamp: int,
        action: RequestAction,
        messages: List[RequestMessage],
        targets: List[str],
        requester_email: str,
        group_email: str,
        reason: str = None,
        approver_email: str = None,
        approval_timestamp: int = None,
        approved: bool = None,
    ) -> None:
        self.request_id: str = request_id
        self.timestamp: int = int(timestamp)
        self.action: RequestAction = action
        self.messages: List[RequestMessage] = messages
        self.targets: List[str] = targets
        self.requester_email: str = requester_email
        self.group_email: str = group_email
        self.reason: Optional[str] = reason
        self.approver_email: Optional[str] = approver_email
        self.approval_timestamp: Optional[int] = int(
            approval_timestamp
        ) if approval_timestamp else None
        self.approved = approved

    @classmethod
    def from_db(cls, db_data: Dict[str, any]) -> "Request":
        db_data["messages"] = [RequestMessage.from_db(msg) for msg in db_data["messages"]]
        return cls(**db_data)

    def add_message(self, message: RequestMessage) -> None:
        self.messages.append(message)

    def to_dict(self) -> Dict[str, any]:
        # Vars is not a new dict, it is a proxy to this object
        # If you were to write changes to it, they would update the object!
        d = dict(vars(self))
        d.update(messages=[vars(m) for m in self.messages])
        return d

    @property
    def messages_json(self) -> str:
        return dumps([vars(msg) for msg in self.messages])

    @property
    def targets_json(self) -> str:
        return dumps(self.targets)

    @property
    def recall_text(self) -> str:
        if self.action == RequestActions.CreateGroup:
            return f"create group {self.group_email}"
        if self.action == RequestActions.JoinGroup:
            who = (
                "yourself"
                if len(self.targets) == 1 and self.requester_email == self.targets[0]
                else ", ".join(t for t in self.targets)
            )
            return f"add {who} to {self.group_email}"
        if self.action == RequestActions.BecomeGroupOwner:
            return f"become an owner of {self.group_email}"

    @property
    def heads_up_text(self) -> str:
        if self.action == RequestActions.CreateGroup:
            return f"added to the new group {self.group_email}"
        if self.action == RequestActions.JoinGroup:
            return f"added to the group {self.group_email}"
        if self.action == RequestActions.BecomeGroupOwner:
            return f"promoted to an owner of the group {self.group_email}"
        if self.action == RequestActions.LeaveGroup:
            return f"removed from the group {self.group_email}"
