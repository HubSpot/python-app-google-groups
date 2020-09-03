from typing import Dict, List, Optional

from ..models import LDAPUser
from ..tasks import LDAPLoadTask


class LDAPController(object):
    def __init__(self, load_task: LDAPLoadTask) -> None:
        self._load_task = load_task
        self._slack_id_map: Dict[str, LDAPUser] = {}
        self._email_map: Dict[str, LDAPUser] = {}

    async def sync(self) -> None:
        # TODO task error resiliency
        async for user in self._load_task.run():
            if "slack" not in user.aliases:
                continue

            self._slack_id_map[user.aliases["slack"]] = user
            self._email_map[user.email] = user

    def get_user_from_slack(self, slack_id: str) -> Optional[LDAPUser]:
        return self._slack_id_map.get(slack_id, None)

    def get_user_from_email(self, email: str) -> Optional[LDAPUser]:
        return self._email_map.get(email, None)

    def get_user_from_aliases(self, aliases: List[str]) -> Optional[LDAPUser]:
        for alias in aliases:
            user = self.get_user_from_email(alias)
            if user:
                return user
