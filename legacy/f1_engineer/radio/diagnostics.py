from __future__ import annotations

import json
import logging
import os
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from ..diagnostics import exception_trace


_active = None
_output_lock = threading.RLock()


def _redirected(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0)
                                               & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


class RadioEventLog:
    def __init__(self, path, *, max_bytes=25 * 1024 * 1024, backups=4):
        path = Path(path)
        if path.suffix != ".jsonl" or any(_redirected(parent) for parent in (path, *path.parents)):
            raise ValueError("radio_log_requires_non_redirected_jsonl_path")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.run_id = uuid.uuid4().hex
        self.sequence = 0
        self.started_ns = time.monotonic_ns()

    def write(self, event):
        self.sequence += 1
        now = time.monotonic_ns()
        value = {**event, "trace": {"run_id": self.run_id, "sequence": self.sequence,
                 "utc": datetime.now(timezone.utc).isoformat(), "monotonic_ns": now,
                 "run_elapsed_s": (now - self.started_ns) / 1e9,
                 "pid": os.getpid(), "thread": threading.current_thread().name}}
        line = json.dumps(value, separators=(",", ":"))
        self.handler.emit(logging.LogRecord("radio", logging.INFO, "", 0, line, (), None))
        return line

    def close(self):
        self.handler.close()


def emit(event):
    with _output_lock:
        line = _active.write(event) if _active is not None else json.dumps(event, separators=(",", ":"))
        print(line, flush=True)


def trace_event(event):
    with _output_lock:
        if _active is not None:
            _active.write(event)


def trace_enabled():
    return _active is not None


@contextmanager
def radio_event_log(path):
    global _active
    if path is None:
        yield
        return
    log = RadioEventLog(path)
    previous = _active
    original_hook = threading.excepthook
    def thread_failure(args):
        emit({"event": "radio_thread_failure", "thread": args.thread.name,
              "exception_chain": exception_trace(args.exc_value)})
        original_hook(args)
    with _output_lock:
        _active = log
        threading.excepthook = thread_failure
    try:
        emit({"event": "radio_log_started", "max_file_bytes": log.handler.maxBytes,
              "backup_files": log.handler.backupCount,
              "contains_spoken_text": True, "raw_audio_logged": False,
              "credentials_logged": False})
        yield
    except BaseException as exception:
        emit({"event": "radio_process_failure", "exception_chain": exception_trace(exception)})
        raise
    finally:
        try:
            emit({"event": "radio_log_closed"})
        finally:
            with _output_lock:
                threading.excepthook = original_hook
                _active = previous
                log.close()
