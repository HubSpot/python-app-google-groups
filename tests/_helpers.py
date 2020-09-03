import unittest
from asyncio import get_event_loop
from datetime import datetime, timezone
from logging import basicConfig
from random import Random, randint

INT_MAX = 2147483646
LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890-_+ "
TLD = "hubspottest.com"


class BaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.seed: int = randint(1, INT_MAX)
        self.rand: Random = Random(self.seed)

        # self.loop is required to use aiohttp's unittest_run_loop
        self.loop = get_event_loop()
        self.run = self.loop.run_until_complete
        basicConfig()

    def _randstring(self, length: int) -> str:
        return "".join(self.rand.choices(LETTERS, k=length))

    def _time_now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def _randemail(self) -> str:
        return f"{self._randstring(32)}@{TLD}"

    def _randbool(self) -> bool:
        return self.rand.random() > 0.5
