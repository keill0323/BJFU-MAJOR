"""不含正文的删除清单：删除前落盘，数据库备份恢复后按身份核对重放。

生产恢复流程应先关闭聊天读写，恢复数据库，然后运行 apply，成功后才开放。
清单须保存在数据库备份之外的持久卷；数据库回滚不能同时回滚本文件。
append 记录已授权删除的意图，数据库事务失败也保留记录以优先保证删除。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading

from sqlalchemy import func

from app.config import settings
from app.models.chat import ChatConversation, ChatEvent, ChatMessage


class LedgerError(RuntimeError):
    """清单缺失、损坏或无法持久化；调用方必须中止正文删除。"""


_lock = threading.Lock()
_FIELDS = {"cid", "id", "sender", "client_id", "hash", "created_at", "deleted_at", "state"}
_STATES = {"purged", "recalled", "hidden"}
_MAX_LINE = 2048
_MAX_ID = 2 ** 63 - 1
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_CLIENT = re.compile(r"^[a-zA-Z0-9_-]{16,64}$")
_HASH = re.compile(r"^[a-f0-9]{64}$")


def _utc(value):
    if not isinstance(value, datetime):
        raise LedgerError("删除清单的时间必须是 datetime")
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo is not None else value


def _timestamp(value):
    return _utc(value).isoformat(timespec="microseconds") + "Z"


def _parse_timestamp(value):
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise LedgerError("删除清单的时间格式不合法")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise LedgerError("删除清单包含不存在的日期") from exc


def _validate(record):
    if not isinstance(record, dict) or set(record) != _FIELDS:
        raise LedgerError("删除清单字段不完整或包含未允许的字段")
    for key in ("id", "cid", "sender"):
        if type(record[key]) is not int or not 0 < record[key] <= _MAX_ID:
            raise LedgerError("删除清单的消息身份不合法")
    if not isinstance(record["client_id"], str) or not _CLIENT.fullmatch(record["client_id"]):
        raise LedgerError("删除清单的客户端编号不合法")
    if not isinstance(record["hash"], str) or not _HASH.fullmatch(record["hash"]):
        raise LedgerError("删除清单的内容摘要不合法")
    if not isinstance(record["state"], str) or record["state"] not in _STATES:
        raise LedgerError("删除清单的删除状态不合法")
    created, deleted = _parse_timestamp(record["created_at"]), _parse_timestamp(record["deleted_at"])
    if deleted < created:
        raise LedgerError("删除时间早于消息创建时间")
    return created, deleted


def _path(value):
    if value is None or not str(value).strip():
        raise LedgerError("未配置 CHAT_DELETION_LEDGER，保留聊天正文并中止删除")
    return Path(str(value)).expanduser().absolute()


def _sync_directory(directory):
    # Windows 不支持打开目录进行 fsync；Linux 生产同步目录项保证新文件可恢复。
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def append(message, at, state):
    """必须在同一数据库事务清除正文之前调用；成功返回才允许继续删除。

    单进程内使用线程锁，追加完整 JSON 行并 fsync。跨进程部署须使用共享
    删除服务或文件锁，不能仅靠本模块的线程锁保证多个写入者并发。
    """
    record = {"cid": message.conversation_id, "id": message.id, "sender": message.sender_id,
              "client_id": message.client_id, "hash": message.payload_hash,
              "created_at": _timestamp(message.created_at), "deleted_at": _timestamp(at), "state": state}
    _validate(record)
    path = _path(getattr(settings, "CHAT_DELETION_LEDGER", ""))
    data = (json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
    if len(data) > _MAX_LINE:
        raise LedgerError("删除清单记录超过长度限制")
    with _lock:
        fd = None
        try:
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            for directory in reversed(missing):
                _sync_directory(directory.parent)
            existed = path.exists()
            flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise LedgerError("删除清单必须是普通文件")
            if info.st_size:
                os.lseek(fd, -1, os.SEEK_END)
                if os.read(fd, 1) != b"\n":
                    raise LedgerError("删除清单尾部不完整，保留正文并中止删除")
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("无法完整追加删除清单")
                view = view[written:]
            os.fsync(fd)
            if not existed:
                _sync_directory(path.parent)
        except OSError as exc:
            raise LedgerError("删除清单无法写入或同步，保留正文并中止删除") from exc
        finally:
            if fd is not None:
                os.close(fd)
    return record


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise LedgerError("删除清单包含重复字段")
        value[key] = item
    return value


def _records(stream):
    number = 0
    while True:
        line = stream.readline(_MAX_LINE + 1)
        if not line:
            return
        number += 1
        try:
            if len(line) > _MAX_LINE or not line.endswith(b"\n"):
                raise LedgerError("记录超长或尾部不完整")
            record = json.loads(line.decode("ascii"), object_pairs_hook=_unique_object)
            created, deleted = _validate(record)
        except (ValueError, UnicodeError, LedgerError) as exc:
            raise LedgerError(f"删除清单第 {number} 行不合法，停止恢复") from exc
        yield record, created, deleted


def restore(db, path):
    """流式重放；每条匹配记录与其 delete 事件在同一事务提交，支持重试。

    匹配消息 id、会话、发送者、客户端编号、原请求摘要与完整创建时间。
    损坏行中止后续处理；此前正确行已提交，不会重新恢复正文。不能在持有
    其他未提交业务写入的会话中调用，也不能边恢复边开放聊天。
    """
    path = _path(path)
    if db.new or db.dirty or db.deleted:
        raise LedgerError("删除清单恢复必须使用没有待提交业务写入的数据库会话")
    result = {"records": 0, "applied": 0, "missing": 0, "mismatched": 0, "already_removed": 0}
    try:
        with _lock, path.open("rb") as stream:
            for record, created, deleted in _records(stream):
                result["records"] += 1
                try:
                    room = db.query(ChatConversation).filter_by(id=record["cid"]).populate_existing().with_for_update().first()
                    message = db.query(ChatMessage).filter_by(id=record["id"]).populate_existing().with_for_update().first()
                    if not room or not message:
                        result["missing"] += 1
                    elif (message.conversation_id != record["cid"] or message.sender_id != record["sender"]
                          or message.client_id != record["client_id"] or message.payload_hash != record["hash"]
                          or _utc(message.created_at) != created):
                        result["mismatched"] += 1
                    elif message.state != "active":
                        result["already_removed"] += 1
                    else:
                        # 已恢复的旧备份可能具有更低的序号；取数据库现有水位避免冲突。
                        event_seq = db.query(func.max(ChatEvent.seq)).filter_by(conversation_id=room.id).scalar() or 0
                        message_seq = db.query(func.max(ChatMessage.seq)).filter_by(conversation_id=room.id).scalar() or 0
                        room.last_seq = max(room.last_seq or 0, event_seq, message_seq) + 1
                        message.content, message.state, message.purged_at = None, "purged", deleted
                        db.add(ChatEvent(conversation_id=room.id, seq=room.last_seq, kind="delete", message_id=message.id,
                                         created_at=datetime.now(timezone.utc).replace(tzinfo=None), notified=False))
                        result["applied"] += 1
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
    except OSError as exc:
        raise LedgerError("删除清单不存在或无法读取，停止恢复") from exc
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="恢复数据库备份后应用聊天删除清单")
    subparsers = parser.add_subparsers(dest="command", required=True)
    apply = subparsers.add_parser("apply", help="先关闭聊天读写，再对恢复后的数据库应用删除记录")
    apply.add_argument("--ledger", required=True, help="独立持久卷中的聊天删除清单路径")
    args = parser.parse_args(argv)
    from app.database import SessionLocal
    try:
        with SessionLocal() as db:
            result = restore(db, args.ledger)
        print("删除清单应用完成：读取 {records} 条，删除正文 {applied} 条，记录缺失 {missing} 条，身份不匹配 {mismatched} 条，已删除 {already_removed} 条。".format(**result))
        return 0
    except LedgerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("删除清单应用失败，请检查数据库；聊天应保持关闭。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
