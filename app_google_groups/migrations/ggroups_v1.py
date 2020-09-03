from aiomysql import Cursor

from ..models import GoogleGroup, GoogleGroupMember

TABLE_NAME_GROUP = GoogleGroup.table_name
TABLE_NAME_GROUP_ALIASES = GoogleGroup.table_name_aliases
TABLE_NAME_MEMBERS = GoogleGroupMember.table_name


async def upgrade(cur: Cursor) -> None:
    # Varchar size of 63 accounts for multi-byte characters
    # and keeps the actual byte size < 255
    # Request ID will be a uuid hex string
    await cur.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME_GROUP} (
            group_id    varchar(63) NOT NULL PRIMARY KEY,
            name        varchar(1024) NOT NULL,
            email       varchar(512) NOT NULL,
            description varchar(1024) NOT NULL,
            etag        varchar(512) NOT NULL,
            protected   boolean NOT NULL
        );"""
    )

    await cur.execute(
        # Primary keys need to be < 3072 bytes
        f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME_GROUP_ALIASES} (
            email       varchar(512) NOT NULL PRIMARY KEY,
            group_id    varchar(63) NOT NULL,
            FOREIGN KEY (group_id) REFERENCES {TABLE_NAME_GROUP}(group_id) ON DELETE CASCADE
        );"""
    )

    await cur.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME_MEMBERS} (
            member_id           varchar(63) NOT NULL,
            group_id            varchar(63) NOT NULL,
            email               varchar(512) NOT NULL,
            member_type         varchar(63) NOT NULL,
            role                varchar(63) NOT NULL,
            status              varchar(63) NOT NULL,
            etag                varchar(512) NOT NULL,
            delivery_settings   varchar(63) NOT NULL,
            PRIMARY KEY (member_id, group_id),
            FOREIGN KEY (group_id) REFERENCES {TABLE_NAME_GROUP}(group_id) ON DELETE CASCADE
        );"""
    )
