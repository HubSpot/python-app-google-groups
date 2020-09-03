from asyncio import AbstractEventLoop
from typing import Awaitable

from aiomysql import Pool, create_pool

from .._dbcreds import DB_HOST, DB_NAME, DB_PASS, DB_USER


def get_pool(loop: AbstractEventLoop) -> Awaitable[Pool]:
    return create_pool(
        host=DB_HOST,
        port=3306,
        user=DB_USER,
        password=DB_PASS,
        db=DB_NAME,
        loop=loop,
        charset="utf8mb4",
        use_unicode=True,
        echo=True,
    )
