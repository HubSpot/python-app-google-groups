from aiomysql import Cursor

from ..models import Request

TABLE_NAME = Request.table_name


async def upgrade(cur: Cursor) -> None:
    await cur.execute(
        # Varchar size of 63 accounts for multi-byte characters
        # and keeps the actual byte size < 255
        # Request ID will be a uuid hex string
        f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            request_id          varchar(32) NOT NULL PRIMARY KEY,
            timestamp           int NOT NULL,
            action              varchar(63) NOT NULL,
            messages            json NOT NULL,
            user_email          varchar(1024) NOT NULL,
            group_email         varchar(1024) NOT NULL,
            approver_email      varchar(1024) NULL,
            approval_timestamp  int NULL,
            approved            boolean NULL
        );"""
    )
