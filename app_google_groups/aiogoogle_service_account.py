# Implementation as per
# https://developers.google.com/identity/protocols/OAuth2ServiceAccount#authorizingrequests
from typing import Any, Dict, Optional

from aiogoogle import Aiogoogle, GoogleAPI
from aiogoogle.auth.managers import JWT_GRANT_TYPE, URLENCODED_CONTENT_TYPE, Oauth2Manager
from aiogoogle.models import Request
from aiogoogle.sessions.aiohttp_session import AiohttpSession
from google.oauth2.service_account import Credentials


# This may seem like overkill..but the cryptography required
# is not worth re implementing
class CredentialsExposed(Credentials):
    def make_authorization_grant_assertion(self) -> bytes:
        return self._make_authorization_grant_assertion()


class ServiceAccountManager(Oauth2Manager):
    def __init__(
        self,
        session_factory: AiohttpSession = AiohttpSession,
        verify: bool = True,
        client_creds: Optional[Dict[str, str]] = None,
    ) -> None:
        super(ServiceAccountManager, self).__init__(
            session_factory=session_factory, verify=verify, client_creds=client_creds
        )

        # The delegated_user and scopes are absolutely required,
        # the token alone doesn't get you much access
        self._credentials = CredentialsExposed.from_service_account_info(
            client_creds, scopes=client_creds["scopes"], subject=client_creds["delegated_user"]
        )

    def is_ready(self, client_creds: Dict[str, Any] = None) -> bool:
        client_creds = client_creds or self.client_creds
        return all(
            k in client_creds
            for k in [
                "scopes",
                "client_id",
                "private_key",
                "project_id",
                "token_uri",
                "client_email",
                "delegated_user",
            ]
        ) and isinstance(client_creds["scopes"], (list, tuple))

    def authorization_url(self, *args: any, **kwargs: any) -> None:
        raise AssertionError("Authorization URL unnecessary for service account auth")

    async def refresh(self, client_creds: Dict[str, str] = None) -> Dict[str, str]:
        client_creds = client_creds or self.client_creds
        assertion = self._credentials.make_authorization_grant_assertion()
        request = Request(
            url=client_creds["token_uri"],
            method="POST",
            headers={"Content-Type": URLENCODED_CONTENT_TYPE},
            data={"grant_type": JWT_GRANT_TYPE, "assertion": assertion.decode()},
        )
        json_res: Dict[str, any] = await self._send_request(request)

        # The _build_user_creds_from_res needs the scope field filled in
        json_res["scope"] = " ".join(self._credentials.scopes)
        return self._build_user_creds_from_res(json_res)


class AiogoogleServiceAccount(Aiogoogle):
    def __init__(
        self,
        session_factory: AiohttpSession = AiohttpSession,
        api_key: Optional[str] = None,
        user_creds: Optional[Dict[str, any]] = None,
        client_creds: Optional[Dict[str, any]] = None,
    ) -> None:
        super(AiogoogleServiceAccount, self).__init__(
            session_factory=session_factory,
            api_key=api_key,
            user_creds=user_creds,
            client_creds=client_creds,
        )

        # Replace oauth2 manager with our manager
        self.oauth2 = ServiceAccountManager(
            session_factory=self.session_factory, client_creds=client_creds
        )

        self._discovery_cache: Dict[str, GoogleAPI] = {}

    async def __aenter__(self) -> "AiogoogleServiceAccount":
        """
        Sets up the API and requests user credentials if necessary
        """
        await super(AiogoogleServiceAccount, self).__aenter__()

        # Ensure user_creds are populated
        # Expiry checks happen when actual requests are made
        if not self.user_creds:
            self.user_creds = await self.oauth2.refresh()

        return self

    async def discover(
        self, api_name: str, api_version: Optional[str] = None, validate: bool = True
    ) -> GoogleAPI:
        """
        Caching version of the regular discover function.
        This isn't actually useful because the self._connector can't be reused
        so you need a new instance of the whole class for each request
        """
        key = f"${api_name}/${api_version}"

        if key not in self._discovery_cache:
            self._discovery_cache[key] = await super(AiogoogleServiceAccount, self).discover(
                api_name=api_name, api_version=api_version, validate=validate
            )

        return self._discovery_cache[key]
