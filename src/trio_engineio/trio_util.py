# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

"""
From: https://github.com/groove-x/trio-util/issues/20
with code in https://gist.github.com/arthur-tacca/32c9b5fa81294850cabc890f4a898a4e

See also (older proposal): https://github.com/python-trio/trio/issues/892
"""
import trio


class TaskNotDoneException(Exception):
    pass


class TaskWrappedException(Exception):
    pass


class ResultCapture:
    """Captures the result of a task for later access.

    If you are directly awaiting a task then there is no need to use this class, you
    can just use the return value:

        result1 = await foo1()
        result2 = await foo2()
        print("results:", result1, result2)

    If you want to run your tasks in parallel then you would typically use a nursery,
    but then it's harder to get hold of the results:

        async with trio.open_nursery() as n:
            n.start_soon(foo1)
            n.start_soon(foo2)
        # At this point the tasks have completed, but the results are lost
        print("results: ??")

    To get access to the results, the routines need to stash their results somewhere for
    you to access later. ResultCapture is a simple helper to do this.

        async with trio.open_nursery() as n:
            r1 = ResultCapture.start_soon(n, foo1)
            r2 = ResultCapture.start_soon(n, foo2)
        # At this point the tasks have completed, and results are stashed in
        # ResultCapture objects print("results", r1.result, r2.result)

    You can get very similar effect to asyncio's gather() function by using a nursery
    and an array of ResultCapture objects:

        async with trio.open_nursery() as n:
            rs = [
                ResultCapture.start_soon(n, foo, i)
                for i in range(10)
            ]
        print("results:", *[r.result for r in rs])

    But ResultCapture is more flexible than gather e.g. you can use a dictionary with
    suitable key rather than an array. You also benefit from the safer behaviour of
    Trio nurseries compared to asyncio's gather.

    Any exception thrown by the task will propagate out as usual, typically to the
    enclosing nursery. Accessing the result attribute will then raise
    CapturedResultException, with the original exception available as the __cause__
    attribute (because it is raised using raise ... from syntax).
    """

    @classmethod
    def start_soon(cls: type, nursery: trio.Nursery, routine, *args):
        """Runs the task in the given nursery and captures its result."""
        task = cls(routine, *args)
        nursery.start_soon(task.run)
        return task

    def __init__(self, routine, *args):
        self._routine = routine
        self._args = args
        self._has_run_been_called = False
        self._done_event = trio.Event()
        self._result = None
        self._exception = None

    async def run(self):
        """Runs the routine and captures its result.

        Typically, you would use the start_soon() class method, which constructs the
        ResultCapture and arranges for the run() method to be run in the given nursery
        But it is possible to manually construct the object and call the run() method
        in situations where that extra control is useful.
        """
        if self._has_run_been_called:
            raise RuntimeError("ResultCapture.run() called multiple times")
        self._has_run_been_called = True
        try:
            self._result = await self._routine(*self._args)
        except BaseException as exc:
            self._exception = exc
            raise  # Note the exception is allowed to propagate into user nursery
        finally:
            self._done_event.set()

    @property
    def result(self):
        """Returns the captured result of the task."""
        if not self._done_event.is_set():
            raise TaskNotDoneException(self)
        if self._exception is not None:
            raise TaskWrappedException(self) from self._exception
        return self._result

    @property
    def exception(self):
        """Returns the exception raised by the task.

        If the task completed by returning rather than raising an exception then this
        returns None. If the task is not done yet then this raises
        TaskNotCompletedException.

        This property returns the original unmodified exception. That is unlike the
        result property, which raises a TaskWrappedException instead, with the
        __cause__ attribute set to the original exception.

        It is usually better design to use the result property and catch exception it
        throws. However, this property can be useful in some situations e.g. filtering
        a list of TaskResult objects.
        """
        if not self._done_event.is_set():
            raise TaskNotDoneException(self)
        return self._exception

    @property
    def done(self):
        """Returns whether the task is done i.e. the result (or an exception) is
        captured.
        """
        return self._done_event.is_set()

    async def wait_done(self):
        """Waits until the task is done.

        There are specialised situations where it may be useful to use this method to
        wait until the task is done, typically where you are writing library code and
        you want to start a routine in a user supplied nursery but wait for it in some
        other context.

        Typically, though, it is much better design to wait for the task's nursery to
        complete. Consider a nursery-based approach before using this method.
        """
        await self._done_event.wait()
