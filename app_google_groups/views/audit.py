from csv import DictWriter
from datetime import datetime, timezone
from io import StringIO

from aiohttp.web import Request, Response, View

from ..controllers import RequestController
from ..models.request import DATE_FORMAT, DEFAULT_AUDIT_RANGE


class AuditView(View):
    def __init__(self, request: Request) -> None:
        super().__init__(request)

        # This class will be instantiated for each request. This means you must
        # bring singletons in scope from the request or app context like so.
        # These singletons are initialised in the main.py
        self._controller: RequestController = request.app["RequestController"]

    async def get(self) -> Response:
        query_args = self.request.query
        token = query_args.get("token")

        if self._controller.check_token(token):
            now = datetime.now(tz=timezone.utc)
            before_raw = query_args.get("before")
            after_raw = query_args.get("after")
            before: datetime = before_raw and datetime.strptime(before_raw, DATE_FORMAT) or now
            after: datetime = after_raw and datetime.strptime(after_raw, DATE_FORMAT) or (
                now - DEFAULT_AUDIT_RANGE
            )

            before_ts = before.timestamp()
            after_ts = after.timestamp()

            with StringIO() as csv_data:
                writer: DictWriter = None
                async for request in self._controller.get_date_range(before_ts, after_ts):
                    if writer is None:
                        writer = DictWriter(csv_data, request.to_dict().keys())
                        writer.writeheader()

                    writer.writerow(request.to_dict())

                return Response(
                    text=csv_data.getvalue(),
                    content_type="text/csv",
                    headers={
                        "Content-Disposition": "attachment;filename="
                        f"ggroups_audit_{after_raw}-{before_raw}.csv"
                    },
                )

        return Response(text="Invalid token", status=403)
