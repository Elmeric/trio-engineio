# Copyright (c) 2022, Eric Lemoine
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import pytest
import trio

from trio_engineio.trio_util import (
    ResultCapture,
    TaskWrappedException,
    TaskNotDoneException,
)


async def fn(arg):
    await trio.sleep(1)
    if arg == 0:
        raise ValueError()
    return 2 * arg


class TestResultCapture:
    async def test_start_soon(self, nursery):
        t: ResultCapture = ResultCapture.start_soon(nursery, fn, 1)

        assert t._routine == fn
        assert t._args == (1,)
        assert not t._has_run_been_called
        assert not t._done_event.is_set()
        assert t._result is None
        assert t._exception is None

    async def test_run_already_called(self, autojump_clock):
        async with trio.open_nursery() as nursery:
            t: ResultCapture = ResultCapture.start_soon(nursery, fn, 1)
            await t.wait_done()
            with pytest.raises(
                RuntimeError, match=r"ResultCapture.run\(\) called multiple times"
            ):
                await t.run()

    async def test_result(self, nursery, autojump_clock):
        t: ResultCapture = ResultCapture.start_soon(nursery, fn, 1)
        await t.wait_done()

        assert t.result == 2
        assert t.done

    async def test_result_not_done(self, nursery, autojump_clock):
        t: ResultCapture = ResultCapture.start_soon(nursery, fn, 1)

        with pytest.raises(TaskNotDoneException):
            _result = t.result

    async def test_result_exception(self, autojump_clock):
        try:
            async with trio.open_nursery() as nursery:
                t: ResultCapture = ResultCapture.start_soon(nursery, fn, 0)
            await t.wait_done()
        except BaseException:
            pass
        finally:
            with pytest.raises(TaskWrappedException):
                _result = t.result

    async def test_exception(self, autojump_clock):
        try:
            async with trio.open_nursery() as nursery:
                t: ResultCapture = ResultCapture.start_soon(nursery, fn, 0)
            await t.wait_done()
        except BaseException:
            pass
        finally:
            assert isinstance(t.exception, ValueError)

    async def test_exception_not_done(self, nursery, autojump_clock):
        t: ResultCapture = ResultCapture.start_soon(nursery, fn, 1)

        with pytest.raises(TaskNotDoneException):
            _exc = t.exception
