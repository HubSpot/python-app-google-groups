from aiohttp import web

from .views import AuditView, SlackActionView, SlackEventView, StatusView


def setup_routes(app: web.Application) -> None:
    """
    This function will set up the HTTP endpoints for your views.
    There should be no other logic in here. Notice that there is
    no way to pass extra arguments to initialize the views. This
    is why everything must be added to the app context in main.py
    """
    app.router.add_view("/slack/events", SlackEventView)
    app.router.add_view("/slack/actions", SlackActionView)

    app.router.add_view("/status/", StatusView)
    app.router.add_view("/audit", AuditView)
