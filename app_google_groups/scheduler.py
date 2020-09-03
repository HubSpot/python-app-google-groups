from asyncio import gather, sleep
from datetime import datetime, timedelta
from time import time
from traceback import print_exc
from typing import Awaitable, Callable, List


class IntervalTask(object):
    """
    Helper class for the TaskScheduler which wraps calling the function with
    a time tracking system
    """

    def __init__(self, name: str, frequency: int, func: Callable[[], Awaitable[None]]) -> None:
        self.name = name
        self._delta = timedelta(minutes=frequency)
        self._func = func
        self._last_time: datetime = None
        self._next_time: datetime = None
        self.mark_run()

    def mark_run(self) -> None:
        self._last_time = datetime.utcnow()
        self._next_time = self._last_time + self._delta

    @property
    def next_run(self) -> datetime:
        return self._next_time

    async def run(self) -> None:
        print("Running task", self.name)
        start_time = time()
        try:
            await self._func()
        except BaseException:
            print("Error occurred running task", self.name)
            print_exc()

        self.mark_run()
        print(
            "Ran task {} in {:.2f} seconds. Next run at {}".format(
                self.name, time() - start_time, self.next_run
            )
        )


class TaskScheduler(object):
    """
    Simple class which runs a list of awaitables on a given interval
    """

    def __init__(self) -> None:
        self._schedule: List[IntervalTask] = []

    def add_task(self, name: str, frequency: int, func: Callable[[], Awaitable[None]]) -> None:
        """
        Add an awaitable to be called on the given frequency (in minutes)
        Once the run_scheduler function starts this task will begin to be scheduled
        """
        self._schedule.append(IntervalTask(name, frequency, func))

    async def run_scheduler(self) -> None:
        """
        Function which runs forever and calls any tasks on their given intervals
        """
        while True:
            await sleep(1)
            now = datetime.utcnow()
            await gather(*[task.run() for task in self._schedule if task.next_run <= now])

    async def run_all(self) -> None:
        """
        Runs all scheduled tasks now regardless of their intervals
        """
        await gather(*[task.run() for task in self._schedule])
