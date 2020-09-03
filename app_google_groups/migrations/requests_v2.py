from aiomysql import Cursor

from ..models import Request

TABLE_NAME = Request.table_name


async def is_upgradeable(cur: Cursor) -> bool:
    try:
        await cur.execute(f"SELECT user_email FROM {TABLE_NAME} LIMIT 1;")
        return cur.rowcount > 0
    except Exception:
        return False


async def create(cur: Cursor) -> None:
    await cur.execute(
        # Varchar size of 63 accounts for multi-byte characters
        # and keeps the actual byte size < 255
        # Request ID will be a uuid hex string
        f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            request_id          varchar(32) NOT NULL PRIMARY KEY,
            timestamp           int NOT NULL,
            action              varchar(63) NOT NULL,
            messages            json NOT NULL,
            targets             json NOT NULL,
            requester_email     varchar(1024) NOT NULL,
            group_email         varchar(1024) NOT NULL,
            approver_email      varchar(1024) NULL,
            approval_timestamp  int NULL,
            approved            boolean NULL
        );"""
    )


async def upgrade(cur: Cursor) -> None:
    if await is_upgradeable(cur):
        await cur.execute(
            f"""ALTER TABLE {TABLE_NAME}
                CHANGE user_email requester_email varchar(1024) NOT NULL,
                ADD targets json NOT NULL
            ;"""
        )
    else:
        await create(cur)
