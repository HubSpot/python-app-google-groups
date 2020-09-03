import hmac
from hashlib import sha256
from typing import Callable

from aiohttp.web import Request, Response, middleware

from ..config import SlackConfigSchema

SLACK_SIG_VERSION = "v0"


class SlackVerifyController(object):
    """
    Verify the request comes from slack
    """

    def __init__(self, slack_config: SlackConfigSchema, path: str) -> None:
        self._signing_secret: str = slack_config.signing_secret.encode()
        self.path: str = path.strip("/")

    @middleware
    async def verify_request(
        self, request: Request, handler: Callable[[Request], Response]
    ) -> Response:
        if request.path.strip("/")[: len(self.path)] == self.path:
            timestamp = request.headers.get("X-Slack-Request-Timestamp", None)
            req_sig = request.headers.get("X-Slack-Signature", None)
            if request.has_body and timestamp and req_sig:
                body = f"{SLACK_SIG_VERSION}:{timestamp}:".encode() + await request.read()
                gen_sig = (
                    f"{SLACK_SIG_VERSION}="
                    + hmac.new(key=self._signing_secret, msg=body, digestmod=sha256).hexdigest()
                )

                if hmac.compare_digest(gen_sig, req_sig):
                    return await handler(request)

            return Response(text="Signature verification failed", status=403)

        return await handler(request)
