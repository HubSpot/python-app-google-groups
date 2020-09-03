# Instead of importing your view files directly, add them to here
# Then you can simply "import views"
from .audit import AuditView
from .slackaction import SlackActionView
from .slackevent import SlackEventView
from .status import StatusView

# Always add the imports you are exposing at the module level to __all__
# Always add a trailing slash so that Black makes the list multiline
# and ensure all values are strings
__all__ = [
    "AuditView",
    "SlackActionView",
    "SlackEventView",
    "StatusView",
]
