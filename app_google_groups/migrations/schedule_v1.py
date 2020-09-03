from aiomysql import Cursor

from ..models import ScheduleEvent

TABLE_NAME = ScheduleEvent.table_name


async def upgrade(cur: Cursor) -> None:
    await cur.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            event_id    int NOT NULL PRIMARY KEY AUTO_INCREMENT,
            action_id   varchar(255) NOT NULL,
            timestamp   int NOT NULL,
            payload     json NOT NULL
        );"""
    )
