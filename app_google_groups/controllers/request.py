from datetime import datetime, timezone
from random import choices
from typing import AsyncIterator, Awaitable, Dict, List, NamedTuple, Optional

from ..config import ConfigSchema
from ..integrations import RequestsDatabaseIntegration
from ..models import Request, RequestAction, RequestActions, RequestAuditReport, RequestMessage
from .ldap import LDAPController

TOKEN_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"


class AuditToken(NamedTuple):
    token: str
    permanent: bool

    def __eq__(self, other: any) -> bool:
        return isinstance(other, str) and other == self.token


class RequestController(object):
    def __init__(
        self, requests_db: RequestsDatabaseIntegration, ldap: LDAPController, config: ConfigSchema
    ) -> None:
        self._requests_db: RequestsDatabaseIntegration = requests_db
        self._ldap = ldap
        self._tokens = [AuditToken(token=t, permanent=True) for t in config.audit_tokens]
        self._approvals_channel = config.slack.approvals_channel

    async def _add_request(
        self,
        request_id: str,
        targets: List[str],
        requester_email: str,
        group_email: str,
        messages: List[RequestMessage],
        action: RequestAction,
        reason: str = None,
    ) -> Request:
        request = Request(
            request_id=request_id,
            timestamp=datetime.now(tz=timezone.utc).timestamp(),
            action=action,
            messages=messages,
            targets=targets,
            requester_email=requester_email,
            group_email=group_email,
            reason=reason,
        )
        await self._requests_db.upsert_request(request)
        return request

    def add_join_request(
        self,
        request_id: str,
        targets: List[str],
        requester_email: str,
        group_email: str,
        messages: List[RequestMessage],
        reason: str = None,
    ) -> Awaitable[Request]:
        return self._add_request(
            request_id,
            targets,
            requester_email,
            group_email,
            messages,
            action=RequestActions.JoinGroup,
            reason=reason,
        )

    def add_leave_request(
        self, request_id: str, requester_email: str, group_email: str, targets: List[str] = None
    ) -> Awaitable[Request]:
        now = datetime.now(tz=timezone.utc).timestamp()
        request = Request(
            request_id=request_id,
            timestamp=now,
            action=RequestActions.LeaveGroup,
            messages=[],
            targets=targets or [requester_email],
            requester_email=requester_email,
            group_email=group_email,
            approver_email=requester_email,
            approval_timestamp=now,
            approved=True,
        )
        return self._requests_db.upsert_request(request)

    def add_create_request(
        self,
        request_id: str,
        requester_email: str,
        group_email: str,
        messages: List[RequestMessage],
    ) -> Awaitable[Request]:
        return self._add_request(
            request_id,
            [requester_email],
            requester_email,
            group_email,
            messages,
            action=RequestActions.CreateGroup,
        )

    def add_become_owner_request(
        self,
        request_id: str,
        requester_email: str,
        group_email: str,
        messages: List[RequestMessage],
    ) -> Awaitable[Request]:
        return self._add_request(
            request_id,
            [requester_email],
            requester_email,
            group_email,
            messages,
            action=RequestActions.BecomeGroupOwner,
        )

    def get_request(self, request_id: str) -> Awaitable[Request]:
        return self._requests_db.get_from_id(request_id=request_id)

    def get_date_range(self, before: int, after: int) -> AsyncIterator[Request]:
        return self._requests_db.get_date_range(before=before, after=after)

    def record_request_result(
        self, request_id: str, approver_email: str, approved: bool
    ) -> Awaitable[Request]:
        return self._requests_db.update_request_result(
            request_id=request_id, approver_email=approver_email, approved=approved,
        )

    def add_messages(self, *messages: List[RequestMessage], request_id: str) -> Awaitable[Request]:
        return self._requests_db.insert_messages(*messages, request_id=request_id)

    def check_token(self, token: Optional[str]) -> bool:
        if token:
            matches = [t for t in self._tokens if t == token]
            if matches:
                if not matches[0].permanent:
                    self._tokens.remove(matches[0])
                return True

        return False

    def generate_token(self) -> str:
        token = "".join(choices(TOKEN_CHARS, k=12))
        self._tokens.append(AuditToken(token=token, permanent=False))
        return token

    async def generate_audit_reports(self, before: int, after: int) -> List[RequestAuditReport]:
        """
        Generates an audit report on a per-action basis
        """
        reports: Dict[str, RequestAuditReport] = {}

        async for request in self.get_date_range(before, after):
            # Add the audit report to the reports dict, if not there already
            # Not using setdefault because that would involve instantiating
            # a RequestAuditReport for every request, just to discard it
            if request.action not in reports:
                reports[request.action] = RequestAuditReport(request.action)
            report = reports[request.action]

            report.total += 1

            if request.approved is not None:
                requester = self._ldap.get_user_from_email(request.requester_email)
                approver = self._ldap.get_user_from_email(request.approver_email)
                approved = int(request.approved)
                report.total_approved += approved
                report.total_denied += 1 - approved
                report.add_ack_time(
                    request.approval_timestamp - request.timestamp,
                    ((requester and requester.is_admin) or (approver and approver.is_admin)),
                )

            has_approval_channel = int(
                any(self._approvals_channel == msg.channel for msg in request.messages)
            )

            report.total_escalated += has_approval_channel
            report.total_unescalated += 1 - has_approval_channel

        return sorted(reports.values(), key=lambda r: r.action)
