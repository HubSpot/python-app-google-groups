from aiohttp import web
from aiohttp.web import Response


class StatusView(web.View):
    """
    Simple view which responds to get requests with a 200 OK
    and "OK" in the body.
    """

    async def get(self) -> Response:
        return web.Response(text="OK", content_type="text/plain")
