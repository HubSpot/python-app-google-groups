from typing import AsyncIterable

from bonsai import LDAPSearchScope
from bonsai.asyncio import AIOConnectionPool

from ..config import LDAPConfigSchema
from ..models import LDAPUser

SEARCH_PAGE_SIZE = 100


class LDAPLoadTask(object):
    def __init__(self, ldap_conn_pool: AIOConnectionPool, ldap_config: LDAPConfigSchema) -> None:
        self._ldap_conn_pool = ldap_conn_pool
        self.ldap_search_base = ldap_config.search_base
        self.ldap_admin_groups = ldap_config.admin_groups

    async def run(self) -> AsyncIterable[LDAPUser]:
        # Load all admin usernames first
        # Nested memberOf resolution on user lookups is unreliable
        conn = await self._ldap_conn_pool.get()
        admin_names = set()
        dn_field = LDAPUser.ldap_attribute_map["dn"]
        for group in self.ldap_admin_groups:
            search = await conn.paged_search(
                base=self.ldap_search_base,
                scope=LDAPSearchScope.SUBTREE,
                filter_exp=f"(&(objectClass=user)(memberOf={group}))",
                page_size=SEARCH_PAGE_SIZE,
                attrlist=[dn_field],
            )

            # Taken from asyncio tests in bonsai
            # Iteration of the pages has to be done manually
            while True:
                for raw_user in search:
                    admin_names.add(raw_user[dn_field][0])

                msgid = search.acquire_next_page()
                if msgid is None:
                    break
                search = await conn.get_result(msgid)

        # Load all users with proxyAddresses
        search = await conn.paged_search(
            base=self.ldap_search_base,
            scope=LDAPSearchScope.SUBTREE,
            filter_exp="(&(objectClass=user)(proxyAddresses=slack:*))",
            page_size=SEARCH_PAGE_SIZE,
            attrlist=LDAPUser.ldap_attributes(),
        )
        # Taken from asyncio tests in bonsai
        # Iteration of the pages has to be done manually
        while True:
            for raw_user in search:
                user = LDAPUser.from_ldap(raw_user)
                user.is_admin = user.dn in admin_names
                yield user

            msgid = search.acquire_next_page()
            if msgid is None:
                break
            search = await conn.get_result(msgid)

        await self._ldap_conn_pool.put(conn)
