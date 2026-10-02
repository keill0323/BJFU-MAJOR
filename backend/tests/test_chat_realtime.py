"""私聊实时连接隔离测试：不连生产、不发送微信消息。"""
import asyncio
import json
import threading
import time
import unittest
from unittest.mock import patch

from tests import support  # noqa: F401 — 先加载隔离的数据库配置。
from fastapi import HTTPException, WebSocketDisconnect
from app.services import chat_realtime as realtime


class FakeSocket:
    def __init__(self, frames=(), accept_error=None, hold_accept=False):
        self.incoming = asyncio.Queue()
        for frame in frames:
            self.incoming.put_nowait(frame)
        self.accept_error = accept_error
        self.hold_accept = hold_accept
        self.accept_started = asyncio.Event()
        self.ready = asyncio.Event()
        self.sent = []
        self.closed = []

    async def accept(self):
        self.accept_started.set()
        if self.accept_error:
            raise self.accept_error
        if self.hold_accept:
            await asyncio.Event().wait()

    async def receive_text(self):
        frame = await self.incoming.get()
        if isinstance(frame, BaseException):
            raise frame
        return frame

    async def send_json(self, payload):
        self.sent.append(payload)
        if payload.get("type") == "ready":
            self.ready.set()

    async def close(self, code=1000):
        self.closed.append(code)


class RealtimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        realtime._tickets.clear()
        realtime._rates.clear()
        realtime._connections.clear()
        realtime._handshakes = 0
        self.tasks = []
        self.auth = patch.object(realtime, "authenticate", return_value=7)
        self.auth.start()
        self.addCleanup(self.auth.stop)

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        realtime._tickets.clear()
        realtime._rates.clear()
        realtime._connections.clear()
        realtime._handshakes = 0

    def credential(self, name="test-ticket", expired=False):
        realtime._tickets[name] = ("Bearer isolated-token", time.monotonic() + (-1 if expired else 30))
        return json.dumps({"ticket": name})

    def start(self, socket):
        task = asyncio.create_task(realtime.socket(socket))
        self.tasks.append(task)
        return task

    async def finish(self, socket, task):
        socket.incoming.put_nowait(WebSocketDisconnect(1000))
        await asyncio.wait_for(task, 2)

    async def test_non_object_or_invalid_handshake_is_closed(self):
        for frame in ("null", "[]", "1", '"string"', "{", "x" * 257):
            with self.subTest(frame=frame[:30]):
                socket = FakeSocket([frame])
                await asyncio.wait_for(realtime.socket(socket), 2)
                self.assertTrue(socket.closed)
                self.assertEqual(realtime._handshakes, 0)
                self.assertFalse(realtime._connections)

    async def test_missing_and_expired_tickets_are_rejected(self):
        for frame in (json.dumps({"ticket": "missing"}), self.credential(expired=True)):
            socket = FakeSocket([frame])
            await asyncio.wait_for(realtime.socket(socket), 2)
            self.assertTrue(socket.closed)
            self.assertFalse(socket.ready.is_set())
            self.assertEqual(realtime._handshakes, 0)

    async def test_accept_failure_releases_handshake_slot(self):
        socket = FakeSocket(accept_error=RuntimeError("transport closed"))
        await asyncio.wait_for(realtime.socket(socket), 2)
        self.assertEqual(realtime._handshakes, 0)
        self.assertFalse(realtime._connections)

    async def test_binary_handshake_frame_is_closed(self):
        # Starlette receive_text 对只有 bytes 的 ASGI 帧会抛出 KeyError("text")。
        socket = FakeSocket([KeyError("text")])
        await asyncio.wait_for(realtime.socket(socket), 2)
        self.assertTrue(socket.closed)
        self.assertEqual(realtime._handshakes, 0)

    async def test_cancellation_during_accept_releases_handshake_slot(self):
        socket = FakeSocket(hold_accept=True)
        task = self.start(socket)
        await asyncio.wait_for(socket.accept_started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(realtime._handshakes, 0)

    async def test_authenticated_connection_releases_handshake_and_uses_bounded_queue(self):
        socket = FakeSocket([self.credential()])
        task = self.start(socket)
        await asyncio.wait_for(socket.ready.wait(), 2)
        self.assertEqual(realtime._handshakes, 0)
        self.assertEqual(len(realtime._connections[7]), 1)
        self.assertEqual(next(iter(realtime._connections[7])).maxsize, 64)
        await self.finish(socket, task)
        self.assertNotIn(7, realtime._connections)

    async def test_ticket_can_only_be_consumed_once(self):
        frame = self.credential()
        socket = FakeSocket([frame])
        task = self.start(socket)
        await asyncio.wait_for(socket.ready.wait(), 2)
        duplicate = FakeSocket([frame])
        await asyncio.wait_for(realtime.socket(duplicate), 2)
        self.assertTrue(duplicate.closed)
        self.assertFalse(duplicate.ready.is_set())
        await self.finish(socket, task)

    async def test_per_user_connection_limit(self):
        opened = []
        for index in range(3):
            socket = FakeSocket([self.credential("ticket-" + str(index))])
            task = self.start(socket)
            await asyncio.wait_for(socket.ready.wait(), 2)
            opened.append((socket, task))
        extra = FakeSocket([self.credential("ticket-extra")])
        await asyncio.wait_for(realtime.socket(extra), 2)
        self.assertIn(4008, extra.closed)
        self.assertEqual(len(realtime._connections[7]), 3)
        for socket, task in opened:
            await self.finish(socket, task)

    async def test_global_connection_limit_is_rechecked_after_authentication(self):
        for index in range(499):
            realtime._connections[100 + index // 3].add(asyncio.Queue(maxsize=64))
        barrier = threading.Barrier(2)
        def authenticate_together(authorization):
            barrier.wait(timeout=2)
            return 7
        with patch.object(realtime, "authenticate", side_effect=authenticate_together):
            sockets = [FakeSocket([self.credential("global-ticket-" + str(index))]) for index in range(2)]
            tasks = [self.start(socket) for socket in sockets]
            async def ready_or_rejected():
                while not all(socket.ready.is_set() or task.done() for socket, task in zip(sockets, tasks)):
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(ready_or_rejected(), 3)
            self.assertEqual(sum(map(len, realtime._connections.values())), 500)
            self.assertEqual(sum(socket.ready.is_set() for socket in sockets), 1)
            for socket, task in zip(sockets, tasks):
                if socket.ready.is_set():
                    await self.finish(socket, task)
                else:
                    self.assertTrue(socket.closed)
                    self.assertIn(socket.closed[0], (4008, 1013))
                    await task

    async def test_heartbeat_rechecks_authentication(self):
        socket = FakeSocket([self.credential()])
        task = self.start(socket)
        await asyncio.wait_for(socket.ready.wait(), 2)
        with patch.object(realtime, "authenticate", side_effect=HTTPException(403, "账号暂不可用")):
            socket.incoming.put_nowait("ping")
            await asyncio.wait_for(task, 2)
        self.assertIn(4001, socket.closed)
        self.assertNotIn(7, realtime._connections)

    async def run_worker_once(self, pending_rows):
        acknowledgements = []
        with patch.object(realtime, "clean", return_value=0), \
                patch.object(realtime, "pending", return_value=pending_rows), \
                patch.object(realtime, "acknowledge", side_effect=lambda ids: acknowledgements.append(ids)):
            task = asyncio.create_task(realtime.worker())
            self.tasks.append(task)
            async def completed():
                while not acknowledgements:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(completed(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        return acknowledgements

    async def test_outbox_only_queues_references_and_attempts_are_acknowledged(self):
        queue = asyncio.Queue(maxsize=64)
        realtime._connections[7].add(queue)
        acknowledgements = await self.run_worker_once([(11, 8, "12", 7, 9)])
        self.assertEqual(queue.get_nowait(), {"type": "dirty", "conversation_id": 8, "seq": "12"})
        self.assertEqual(acknowledgements, [[11]])

    async def test_overflow_queue_remains_bounded_and_requests_disconnect(self):
        queue = asyncio.Queue(maxsize=64)
        for index in range(64):
            queue.put_nowait({"type": "dirty", "seq": str(index)})
        realtime._connections[7].add(queue)
        await self.run_worker_once([(11, 8, "12", 7, 9)])
        self.assertLessEqual(queue.qsize(), 64)
        self.assertIsNone(queue.get_nowait())

    async def test_single_conversation_batch_is_coalesced_without_disconnect(self):
        queue = asyncio.Queue(maxsize=64)
        realtime._connections[7].add(queue)
        rows = [(index, 8, str(index), 7, 9) for index in range(1, 101)]
        acknowledgements = await self.run_worker_once(rows)
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(queue.get_nowait(), {"type": "dirty", "conversation_id": 8, "seq": "100"})
        self.assertEqual(acknowledgements, [list(range(1, 101))])

    async def test_cleanup_failure_does_not_block_dirty_or_retry_every_second(self):
        queue = asyncio.Queue(maxsize=64)
        realtime._connections[7].add(queue)
        acknowledgements = []
        with patch.object(realtime, "clean", side_effect=HTTPException(503, "清理存储不可用")) as clean, \
                patch.object(realtime, "pending", return_value=[(11, 8, "12", 7, 9)]), \
                patch.object(realtime, "acknowledge", side_effect=lambda ids: acknowledgements.append(ids)), \
                patch.object(realtime.logger, "warning") as warning:
            task = asyncio.create_task(realtime.worker())
            self.tasks.append(task)
            async def dispatched_twice():
                while len(acknowledgements) < 2:
                    await asyncio.sleep(0.01)
            try:
                await asyncio.wait_for(dispatched_twice(), 3)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            clean.assert_called_once()
            warning.assert_called_once()
        self.assertEqual(acknowledgements, [[11], [11]])
        self.assertEqual(queue.get_nowait(), {"type": "dirty", "conversation_id": 8, "seq": "12"})


if __name__ == "__main__":
    unittest.main()
