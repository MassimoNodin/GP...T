from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import threading
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Protocol
from urllib.parse import unquote_to_bytes

from ..errors import CaptureFormatError
from ..recording.capture import read_capture_metadata
from .import_jobs import (
    MAX_RECORDING_CATALOG_DIRECTORY_ENTRIES,
    RecordingCatalogUnavailable,
    register_recording_source,
)

DEFAULT_MAX_RECORDING_UPLOAD_BYTES = 16 * 1024**3
MAX_RECORDING_UPLOAD_CHUNK_BYTES = 64 * 1024
MAX_RECORDING_UPLOAD_STALL_SECONDS = 60
MAX_RECORDING_UPLOAD_DEADLINE_SECONDS = 30 * 60
_MAX_CAPTURE_HEADER_BYTES = 12 + 1_048_576
_STAGING_NAME = re.compile(r"^\.[a-f0-9]{32}\.f1e-uploading$")
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_ROOT_MARKER_NAME = ".f1e-upload-root"
_WINDOWS_ROOT_MARKER_CONTENT = b"F1-ENGINEER-RECORDING-ROOT-V1\n"


class OperationController(Protocol):
    @property
    def ready(self) -> bool: ...

    def reserve_operation(self, operation: str) -> bool: ...

    def release_operation(self, operation: str) -> None: ...


class RecordingUploadError(RuntimeError):
    def __init__(self, reason: str, status_code: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class RecordingUploadService:
    """Receive one bounded capture into the configured local recordings root."""

    def __init__(
        self,
        database_path: str | Path,
        recordings_root: str | Path,
        operation_controller: OperationController,
        *,
        max_upload_bytes: int = DEFAULT_MAX_RECORDING_UPLOAD_BYTES,
    ) -> None:
        if (
            isinstance(max_upload_bytes, bool)
            or not isinstance(max_upload_bytes, int)
            or not 1 <= max_upload_bytes <= DEFAULT_MAX_RECORDING_UPLOAD_BYTES
        ):
            raise ValueError("max_upload_bytes_invalid")
        self.database_path = Path(database_path).expanduser().resolve()
        self.recordings_root = Path(recordings_root).expanduser().resolve()
        self.operation_controller = operation_controller
        self.max_upload_bytes = max_upload_bytes
        self._root_identity: tuple[int, int] | None = None
        self._ready = False
        self._lock = threading.Lock()
        self._active: set[RecordingUpload] = set()
        self.unavailable_reason: str | None = None

    @property
    def ready(self) -> bool:
        return self._ready and self.operation_controller.ready

    def start(self) -> None:
        if not self.operation_controller.ready:
            self.unavailable_reason = "recording_upload_controller_unavailable"
            return
        try:
            self.recordings_root.mkdir(parents=True, exist_ok=True)
            root_fd = _open_root(self.recordings_root, None)
            try:
                if isinstance(root_fd, _WindowsPinnedRoot):
                    self._root_identity = root_fd.identity
                else:
                    self._root_identity = _root_identity(os.fstat(root_fd))
                self._remove_abandoned_staging(root_fd)
            finally:
                _close_root(root_fd)
        except (OSError, RecordingUploadError):
            self.unavailable_reason = "recording_upload_root_unavailable"
            return
        self.unavailable_reason = None
        self._ready = True

    def close(self) -> None:
        with self._lock:
            active = tuple(self._active)
        for upload in active:
            upload.abort()
        self._ready = False

    def begin_upload(
        self, encoded_filename: str, *, expected_bytes: int | None = None
    ) -> RecordingUpload:
        if not self.ready:
            raise RecordingUploadError(
                "recording_upload_service_unavailable", 503
            )
        display_name = _safe_display_name(encoded_filename)
        if expected_bytes is not None and (
            isinstance(expected_bytes, bool)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 1
        ):
            raise RecordingUploadError("recording_upload_length_invalid", 400)
        if expected_bytes is not None and expected_bytes > self.max_upload_bytes:
            raise RecordingUploadError("recording_upload_size_limit", 413)
        try:
            reserved = self.operation_controller.reserve_operation("upload")
        except ValueError as exc:
            raise RecordingUploadError(
                "recording_upload_service_unavailable", 503
            ) from exc
        if not reserved:
            raise RecordingUploadError("another_local_operation_is_in_progress", 409)

        root_fd: int | _WindowsPinnedRoot | None = None
        stream: BinaryIO | None = None
        stage_created = False
        stage_name = f".{uuid.uuid4().hex}.f1e-uploading"
        final_token = uuid.uuid4().hex
        final_name = f"f1e-upload-{final_token}-{display_name}"
        try:
            root_fd = _open_root(self.recordings_root, self._root_identity)
            stream = _create_stage(self.recordings_root, root_fd, stage_name)
            stage_created = True
            upload = RecordingUpload(
                service=self,
                root_fd=root_fd,
                stream=stream,
                stage_name=stage_name,
                final_name=final_name,
                display_name=final_name,
                expected_bytes=expected_bytes,
            )
            with self._lock:
                self._active.add(upload)
            return upload
        except Exception as exc:
            try:
                if stream is not None and stage_created and root_fd is not None:
                    _unlink_stage(
                        self.recordings_root, root_fd, stage_name, stream=stream
                    )
            except OSError:
                pass
            finally:
                try:
                    if stream is not None and not stream.closed:
                        stream.close()
                except OSError:
                    pass
                finally:
                    try:
                        if root_fd is not None:
                            _close_root(root_fd)
                    finally:
                        self.operation_controller.release_operation("upload")
            if isinstance(exc, RecordingUploadError):
                raise
            raise RecordingUploadError(
                "recording_upload_staging_unavailable", 503
            ) from exc

    def _remove_abandoned_staging(
        self, root_fd: int | _WindowsPinnedRoot
    ) -> None:
        scan_target = self.recordings_root if os.name == "nt" else root_fd
        names: list[str] = []
        with os.scandir(scan_target) as entries:
            for entry in entries:
                if len(names) >= MAX_RECORDING_CATALOG_DIRECTORY_ENTRIES:
                    raise RecordingUploadError(
                        "recording_upload_root_entry_limit", 503
                    )
                names.append(entry.name)
        for name in names:
            if _STAGING_NAME.fullmatch(name) is None:
                continue
            try:
                value = _stat_entry(self.recordings_root, root_fd, name)
                if (
                    stat.S_ISREG(value.st_mode)
                    and not getattr(value, "st_file_attributes", 0)
                    & _REPARSE_ATTRIBUTE
                ):
                    _unlink_stage(self.recordings_root, root_fd, name)
            except OSError:
                continue

    def _finish(self, upload: RecordingUpload) -> dict[str, object]:
        if upload.bytes_received < 1:
            raise RecordingUploadError("recording_upload_body_empty", 422)
        if (
            upload.expected_bytes is not None
            and upload.bytes_received != upload.expected_bytes
        ):
            raise RecordingUploadError("recording_upload_length_mismatch", 400)
        try:
            metadata = read_capture_metadata(io.BytesIO(bytes(upload._header)))
        except (CaptureFormatError, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise RecordingUploadError("recording_upload_capture_header_invalid", 422) from exc
        if not isinstance(metadata.get("schema_version"), int) or isinstance(
            metadata.get("schema_version"), bool
        ):
            raise RecordingUploadError("recording_upload_capture_header_invalid", 422)

        try:
            upload._raise_if_cancelled()
            upload.stream.flush()
            os.fsync(upload.stream.fileno())
            initial = os.fstat(upload.stream.fileno())
            _require_regular(initial)
            upload._raise_if_cancelled()
            _publish_no_replace(
                self.recordings_root,
                upload.root_fd,
                upload.stage_name,
                upload.final_name,
                initial,
                upload.stream,
            )
            upload._published = True
            final_stat = os.fstat(upload.stream.fileno())
            _require_regular(final_stat)
            if not _same_file(initial, final_stat):
                raise RecordingUploadError("recording_upload_publish_changed", 503)
            upload._raise_if_cancelled()
            capture_id = register_recording_source(
                self.database_path,
                self.recordings_root,
                upload.final_name,
                byte_size=upload.bytes_received,
                modified_ns=final_stat.st_mtime_ns,
                expected_root_identity=self._root_identity,
                expected_file_identity=_file_identity(final_stat),
            )
        except RecordingUploadError:
            raise
        except RecordingCatalogUnavailable as exc:
            raise RecordingUploadError(exc.reason, 503) from exc
        except (OSError, ValueError, TypeError) as exc:
            raise RecordingUploadError(
                "recording_upload_publish_unavailable", 503
            ) from exc
        return {
            "capture_id": capture_id,
            "display_name": upload.display_name,
            "byte_size": upload.bytes_received,
            "sha256": upload._digest.hexdigest(),
            "verification_scope": "capture_header_and_transferred_bytes",
            "header_schema_version": metadata["schema_version"],
        }

    def _release(self, upload: RecordingUpload) -> None:
        with self._lock:
            self._active.discard(upload)
        self.operation_controller.release_operation("upload")


@dataclass(eq=False)
class RecordingUpload:
    service: RecordingUploadService
    root_fd: int | _WindowsPinnedRoot
    stream: BinaryIO
    stage_name: str
    final_name: str
    display_name: str
    expected_bytes: int | None
    bytes_received: int = 0
    _header: bytearray = field(default_factory=bytearray)
    _digest: Any = field(default_factory=hashlib.sha256)
    _closed: bool = False
    _published: bool = False
    _cancelled: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def write_chunk(self, value: bytes) -> None:
        if not isinstance(value, bytes):
            raise RecordingUploadError("recording_upload_chunk_invalid", 400)
        if not value:
            return
        with self._lock:
            if self._closed:
                raise RecordingUploadError("recording_upload_closed", 409)
            self._raise_if_cancelled()
            total = self.bytes_received + len(value)
            if total > self.service.max_upload_bytes:
                raise RecordingUploadError("recording_upload_size_limit", 413)
            if self.expected_bytes is not None and total > self.expected_bytes:
                raise RecordingUploadError("recording_upload_length_mismatch", 400)
            view = memoryview(value)
            for offset in range(0, len(view), MAX_RECORDING_UPLOAD_CHUNK_BYTES):
                block = view[offset : offset + MAX_RECORDING_UPLOAD_CHUNK_BYTES]
                written = 0
                while written < len(block):
                    count = self.stream.write(block[written:])
                    if count is None or count <= 0:
                        raise OSError("recording_upload_short_write")
                    written += count
            self._digest.update(value)
            self.bytes_received = total
            remaining = _MAX_CAPTURE_HEADER_BYTES - len(self._header)
            if remaining > 0:
                self._header.extend(value[:remaining])

    def finish(self) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise RecordingUploadError("recording_upload_closed", 409)
            try:
                self._raise_if_cancelled()
                result = self.service._finish(self)
            except Exception:
                self._close_locked()
                raise
            self._close_locked()
            return result

    def abort(self) -> None:
        self.request_cancel()
        with self._lock:
            self._close_locked()

    def request_cancel(self) -> None:
        self._cancelled.set()

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise RecordingUploadError("recording_upload_cancelled", 408)

    def _close_locked(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if not self._published:
                _unlink_stage(
                    self.service.recordings_root,
                    self.root_fd,
                    self.stage_name,
                    stream=self.stream,
                )
        finally:
            try:
                if not self.stream.closed:
                    self.stream.close()
            finally:
                try:
                    _close_root(self.root_fd)
                finally:
                    self.service._release(self)


def _safe_display_name(encoded_filename: str) -> str:
    if not isinstance(encoded_filename, str) or len(encoded_filename) > 2_048:
        raise RecordingUploadError("recording_upload_filename_invalid", 422)
    try:
        raw = unquote_to_bytes(encoded_filename).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise RecordingUploadError("recording_upload_filename_invalid", 422) from exc
    basename = raw.replace("\\", "/").rsplit("/", 1)[-1]
    if not basename.casefold().endswith(".f1ecap"):
        raise RecordingUploadError("recording_upload_filename_invalid", 422)
    stem = basename[: -len(".f1ecap")]
    cleaned = "".join(
        char if (char.isalnum() or char in " ._()-") else "_" for char in stem
    )
    cleaned = " ".join(cleaned.split()).strip(" .")[:160].rstrip(" .")
    return f"{cleaned or 'capture'}.f1ecap"


def _open_root(
    root: Path, expected_identity: tuple[int, int] | None
) -> int | _WindowsPinnedRoot:
    if os.name == "nt":
        return _open_windows_root(root, expected_identity)
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not hasattr(os, flag) for flag in required) or not root.is_absolute():
        raise RecordingUploadError("recording_upload_validation_unavailable", 503)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(root.anchor, flags)
    try:
        for component in root.parts[1:]:
            child_fd = os.open(component, flags, dir_fd=root_fd)
            os.close(root_fd)
            root_fd = child_fd
        value = os.fstat(root_fd)
        identity = _root_identity(value)
        if expected_identity is not None and identity != expected_identity:
            raise RecordingUploadError("recording_upload_root_changed", 503)
        return root_fd
    except Exception:
        os.close(root_fd)
        raise


def _root_identity(value: os.stat_result) -> tuple[int, int]:
    if (
        not stat.S_ISDIR(value.st_mode)
        or getattr(value, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE
        or not value.st_ino
    ):
        raise RecordingUploadError("recording_upload_root_unavailable", 503)
    return value.st_dev, value.st_ino


def _close_root(root_fd: int | _WindowsPinnedRoot) -> None:
    if isinstance(root_fd, _WindowsPinnedRoot):
        root_fd.close()
    else:
        os.close(root_fd)


def _create_stage(
    root: Path, root_fd: int | _WindowsPinnedRoot, name: str
) -> BinaryIO:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    if isinstance(root_fd, _WindowsPinnedRoot):
        return root_fd.create_stage(name)
    flags |= os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=root_fd)
    try:
        _require_regular(os.fstat(descriptor))
        return os.fdopen(descriptor, "w+b", buffering=0)
    except Exception:
        os.close(descriptor)
        raise


def _stat_entry(
    root: Path, root_fd: int | _WindowsPinnedRoot, name: str
) -> os.stat_result:
    if isinstance(root_fd, _WindowsPinnedRoot):
        return root_fd.stat_entry(name)
    return os.stat(name, dir_fd=root_fd, follow_symlinks=False)


def _unlink_stage(
    root: Path,
    root_fd: int | _WindowsPinnedRoot,
    name: str,
    *,
    stream: BinaryIO | None = None,
) -> None:
    try:
        if isinstance(root_fd, _WindowsPinnedRoot):
            root_fd.unlink(name, stream)
        else:
            os.unlink(name, dir_fd=root_fd)
    except FileNotFoundError:
        pass


def _publish_no_replace(
    root: Path,
    root_fd: int | _WindowsPinnedRoot,
    stage_name: str,
    final_name: str,
    initial: os.stat_result,
    stream: BinaryIO,
) -> None:
    if isinstance(root_fd, _WindowsPinnedRoot):
        stage_stat = os.fstat(stream.fileno())
        _require_regular(stage_stat)
        if not _same_file(initial, stage_stat):
            raise RecordingUploadError("recording_upload_staging_changed", 503)
        root_fd.publish(stream, final_name)
        final_stat = os.fstat(stream.fileno())
        _require_regular(final_stat)
        if not _same_file(initial, final_stat):
            raise RecordingUploadError("recording_upload_publish_changed", 503)
        return

    stage_stat = _stat_entry(root, root_fd, stage_name)
    _require_regular(stage_stat)
    if not _same_file(initial, stage_stat):
        raise RecordingUploadError("recording_upload_staging_changed", 503)
    os.link(
        stage_name,
        final_name,
        src_dir_fd=root_fd,
        dst_dir_fd=root_fd,
        follow_symlinks=False,
    )
    final_stat = _stat_entry(root, root_fd, final_name)
    _require_regular(final_stat)
    if not _same_file(initial, final_stat):
        raise RecordingUploadError("recording_upload_publish_changed", 503)
    _unlink_stage(root, root_fd, stage_name)


def _require_regular(value: os.stat_result) -> None:
    if (
        not stat.S_ISREG(value.st_mode)
        or getattr(value, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE
        or value.st_size < 0
        or value.st_mtime_ns < 0
    ):
        raise RecordingUploadError("recording_upload_file_unavailable", 503)


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    if not first.st_ino or not second.st_ino:
        raise RecordingUploadError("recording_upload_validation_unavailable", 503)
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
    )


def _file_identity(value: os.stat_result) -> tuple[int, int]:
    if not value.st_ino:
        raise RecordingUploadError("recording_upload_validation_unavailable", 503)
    return value.st_dev, value.st_ino


class _WindowsPinnedRoot:
    """A directory handle and protected ancestry for relative NT operations."""

    def __init__(
        self,
        handles: list[int],
        root_handle: int,
        identity: tuple[int, int],
        volume_path: str,
        marker_stream: BinaryIO,
    ) -> None:
        self._handles = handles
        self.handle = root_handle
        self.identity = identity
        self.volume_path = volume_path
        self.marker_stream = marker_stream
        self.closed = False

    def create_stage(self, name: str) -> BinaryIO:
        handle = _win_open_relative(
            self.handle,
            name,
            desired_access=0x0001 | 0x0002 | 0x0080 | 0x00010000 | 0x00100000,
            share_access=0x00000001,
            disposition=2,
            create_options=0x00000040 | 0x00200000 | 0x00000020,
            file_attributes=0x00000002,
        )
        try:
            return _win_stream(handle, os.O_RDWR | os.O_BINARY)
        except Exception:
            try:
                self.unlink(name, None)
            except OSError:
                pass
            raise

    def stat_entry(self, name: str) -> os.stat_result:
        stream = _win_stream(
            _win_open_relative(
                self.handle,
                name,
                desired_access=0x0080 | 0x00100000,
                share_access=0x00000001,
                disposition=1,
                create_options=0x00000040 | 0x00200000 | 0x00000020,
            ),
            os.O_RDONLY | os.O_BINARY,
        )
        try:
            return os.fstat(stream.fileno())
        finally:
            stream.close()

    def unlink(self, name: str, stream: BinaryIO | None) -> None:
        if stream is not None and not stream.closed:
            _win_mark_delete(stream.fileno())
            return
        entry = _win_stream(
            _win_open_relative(
                self.handle,
                name,
                desired_access=0x0080 | 0x00010000 | 0x00100000,
                share_access=0x00000001,
                disposition=1,
                create_options=0x00000040 | 0x00200000 | 0x00000020,
            ),
            os.O_RDONLY | os.O_BINARY,
        )
        try:
            info = os.fstat(entry.fileno())
            if stat.S_ISREG(info.st_mode) and not (
                getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE
            ):
                _win_mark_delete(entry.fileno())
        finally:
            entry.close()

    def publish(self, stream: BinaryIO, final_name: str) -> None:
        import ntpath

        destination = ntpath.join(self.volume_path, final_name)
        _win_rename_handle(stream.fileno(), destination)
        try:
            _win_clear_hidden_attribute(stream.fileno())
        except OSError:
            pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        api = _windows_api()
        try:
            self.marker_stream.close()
        finally:
            for handle in reversed(self._handles):
                api["close_handle"](handle)


@lru_cache(maxsize=1)
def _windows_api() -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_long), ("Information", ctypes.c_size_t)]

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("CreationTimeLow", wintypes.DWORD),
            ("CreationTimeHigh", wintypes.DWORD),
            ("LastAccessTimeLow", wintypes.DWORD),
            ("LastAccessTimeHigh", wintypes.DWORD),
            ("LastWriteTimeLow", wintypes.DWORD),
            ("LastWriteTimeHigh", wintypes.DWORD),
            ("VolumeSerialNumber", wintypes.DWORD),
            ("FileSizeHigh", wintypes.DWORD),
            ("FileSizeLow", wintypes.DWORD),
            ("NumberOfLinks", wintypes.DWORD),
            ("FileIndexHigh", wintypes.DWORD),
            ("FileIndexLow", wintypes.DWORD),
        ]

    class RenameFlags(ctypes.Union):
        _fields_ = [("ReplaceIfExists", ctypes.c_ubyte), ("Flags", wintypes.DWORD)]

    class RenameInfo(ctypes.Structure):
        _fields_ = [
            ("RenameFlags", RenameFlags),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * 1),
        ]

    class DispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    class BasicInfo(ctypes.Structure):
        _fields_ = [
            ("CreationTime", ctypes.c_longlong),
            ("LastAccessTime", ctypes.c_longlong),
            ("LastWriteTime", ctypes.c_longlong),
            ("ChangeTime", ctypes.c_longlong),
            ("FileAttributes", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleFileInformation)]
    get_info.restype = wintypes.BOOL
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    set_info.restype = wintypes.BOOL
    nt_create_file = ntdll.NtCreateFile
    nt_create_file.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    nt_create_file.restype = ctypes.c_long
    status_to_error = ntdll.RtlNtStatusToDosError
    status_to_error.argtypes = [ctypes.c_long]
    status_to_error.restype = wintypes.ULONG
    return {
        "ctypes": ctypes,
        "wintypes": wintypes,
        "UnicodeString": UnicodeString,
        "ObjectAttributes": ObjectAttributes,
        "IoStatusBlock": IoStatusBlock,
        "ByHandleFileInformation": ByHandleFileInformation,
        "RenameInfo": RenameInfo,
        "DispositionInfo": DispositionInfo,
        "BasicInfo": BasicInfo,
        "create_file": create_file,
        "close_handle": close_handle,
        "get_final_path": get_final_path,
        "get_info": get_info,
        "set_info": set_info,
        "nt_create_file": nt_create_file,
        "status_to_error": status_to_error,
    }


def _win_handle_value(handle: Any) -> int:
    value = handle if isinstance(handle, int) else handle.value
    invalid = (1 << (_windows_api()["ctypes"].sizeof(_windows_api()["ctypes"].c_void_p) * 8)) - 1
    if value is None or value in {-1, invalid}:
        raise OSError("invalid Windows handle")
    return int(value)


def _win_file_info(handle: int) -> Any:
    api = _windows_api()
    info = api["ByHandleFileInformation"]()
    if not api["get_info"](handle, api["ctypes"].byref(info)):
        raise OSError(api["ctypes"].get_last_error(), "file identity unavailable")
    return info


def _win_open_relative(
    parent: int,
    name: str,
    *,
    desired_access: int,
    share_access: int,
    disposition: int,
    create_options: int,
    file_attributes: int = 0,
) -> int:
    if (
        not name
        or name in {".", ".."}
        or "\\" in name
        or "/" in name
        or "\x00" in name
    ):
        raise RecordingUploadError("recording_upload_filename_invalid", 422)
    api = _windows_api()
    ctypes = api["ctypes"]
    wintypes = api["wintypes"]
    buffer = ctypes.create_unicode_buffer(name)
    unicode_name = api["UnicodeString"](
        len(name.encode("utf-16-le")),
        len(name.encode("utf-16-le")) + 2,
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = api["ObjectAttributes"](
        ctypes.sizeof(api["ObjectAttributes"]),
        wintypes.HANDLE(parent),
        ctypes.pointer(unicode_name),
        0x40,  # OBJ_CASE_INSENSITIVE
        None,
        None,
    )
    io_status = api["IoStatusBlock"]()
    handle = wintypes.HANDLE()
    status = api["nt_create_file"](
        ctypes.byref(handle),
        desired_access,
        ctypes.byref(attributes),
        ctypes.byref(io_status),
        None,
        file_attributes,
        share_access,
        disposition,
        create_options,
        None,
        0,
    )
    if status < 0:
        error = int(api["status_to_error"](status))
        raise ctypes.WinError(error)
    return _win_handle_value(handle)


def _win_stream(handle: int, flags: int) -> BinaryIO:
    import msvcrt

    try:
        descriptor = msvcrt.open_osfhandle(handle, flags)
    except Exception:
        _windows_api()["close_handle"](handle)
        raise
    try:
        return os.fdopen(descriptor, "r+b" if flags & os.O_RDWR else "rb", buffering=0)
    except Exception:
        os.close(descriptor)
        raise


def _win_mark_delete(descriptor: int) -> None:
    import msvcrt

    api = _windows_api()
    info = api["DispositionInfo"](True)
    handle = msvcrt.get_osfhandle(descriptor)
    if not api["set_info"](
        handle, 4, api["ctypes"].byref(info), api["ctypes"].sizeof(info)
    ):
        raise OSError(api["ctypes"].get_last_error(), "file deletion unavailable")


def _win_rename_handle(descriptor: int, destination: str) -> None:
    import msvcrt

    api = _windows_api()
    ctypes = api["ctypes"]
    encoded = destination.encode("utf-16-le")
    info_type = api["RenameInfo"]
    info = info_type()
    info.RenameFlags.Flags = 0
    info.RootDirectory = 0
    info.FileNameLength = len(encoded)
    information_size = ctypes.sizeof(info_type) + len(encoded)
    buffer = ctypes.create_string_buffer(information_size)
    ctypes.memmove(buffer, ctypes.byref(info), info_type.FileName.offset)
    ctypes.memmove(
        ctypes.addressof(buffer) + info_type.FileName.offset, encoded, len(encoded)
    )
    handle = msvcrt.get_osfhandle(descriptor)
    if not api["set_info"](
        handle, 3, buffer, information_size
    ):
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise RecordingUploadError("recording_upload_publish_collision", 503)
        raise OSError(error, "handle-relative publication unavailable")


def _win_final_volume_path(handle: int) -> str:
    api = _windows_api()
    get_path = api["get_final_path"]
    required = get_path(handle, None, 0, 0x1)  # VOLUME_NAME_GUID
    if required <= 0 or required > 32_768:
        raise OSError(api["ctypes"].get_last_error(), "root volume path unavailable")
    buffer = api["ctypes"].create_unicode_buffer(required + 1)
    actual = get_path(handle, buffer, len(buffer), 0x1)
    if actual <= 0 or actual >= len(buffer):
        raise OSError(api["ctypes"].get_last_error(), "root volume path unavailable")
    path = buffer.value
    if not path.startswith("\\\\?\\Volume{"):
        raise RecordingUploadError("recording_upload_validation_unavailable", 503)
    return path.rstrip("\\")


def _win_clear_hidden_attribute(descriptor: int) -> None:
    import msvcrt

    api = _windows_api()
    info = api["BasicInfo"]()
    info.FileAttributes = 0x00000080  # FILE_ATTRIBUTE_NORMAL
    handle = msvcrt.get_osfhandle(descriptor)
    if not api["set_info"](
        handle, 0, api["ctypes"].byref(info), api["ctypes"].sizeof(info)
    ):
        raise OSError(api["ctypes"].get_last_error(), "file attributes unavailable")


def _win_ensure_root_marker(root_handle: int) -> BinaryIO:
    try:
        marker_handle = _win_open_relative(
            root_handle,
            _WINDOWS_ROOT_MARKER_NAME,
            desired_access=0x0001 | 0x0002 | 0x0080 | 0x00010000 | 0x00100000,
            share_access=0x00000001,
            disposition=2,
            create_options=0x00000040 | 0x00200000 | 0x00000020,
            file_attributes=0x00000002,
        )
        created = True
    except OSError as exc:
        if getattr(exc, "winerror", None) not in {80, 183}:
            raise
        marker_handle = _win_open_relative(
            root_handle,
            _WINDOWS_ROOT_MARKER_NAME,
            desired_access=0x0001 | 0x0080 | 0x00100000,
            share_access=0x00000001,
            disposition=1,
            create_options=0x00000040 | 0x00200000 | 0x00000020,
        )
        created = False
    stream = _win_stream(
        marker_handle,
        os.O_RDWR | os.O_BINARY if created else os.O_RDONLY | os.O_BINARY,
    )
    try:
        marker_stat = os.fstat(stream.fileno())
        _require_regular(marker_stat)
        if created:
            stream.write(_WINDOWS_ROOT_MARKER_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
        if stream.read(len(_WINDOWS_ROOT_MARKER_CONTENT) + 1) != (
            _WINDOWS_ROOT_MARKER_CONTENT
        ):
            raise RecordingUploadError("recording_upload_root_marker_invalid", 503)
        return stream
    except Exception:
        if created:
            try:
                _win_mark_delete(stream.fileno())
            except OSError:
                pass
        stream.close()
        raise


def _open_windows_root(
    root: Path, expected_identity: tuple[int, int] | None
) -> _WindowsPinnedRoot:
    import ntpath

    if not root.is_absolute() or not root.drive or root.drive.startswith("\\\\"):
        raise RecordingUploadError("recording_upload_validation_unavailable", 503)
    api = _windows_api()
    handles: list[int] = []
    marker_stream: BinaryIO | None = None
    anchor = root.anchor
    anchor_handle = _win_handle_value(
        api["create_file"](
            anchor,
            0x00000001 | 0x00000080,  # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
            0x00000001 | 0x00000002,  # share read/write, deny delete
            None,
            3,
            0x00200000 | 0x02000000,  # OPEN_REPARSE_POINT | BACKUP_SEMANTICS
            None,
        )
    )
    handles.append(anchor_handle)
    current_handle = anchor_handle
    try:
        components = root.parts[1:]
        for index, component in enumerate(components):
            final = index == len(components) - 1
            access = 0x00000001 | 0x00000020 | 0x00000080 | 0x00100000
            if final:
                access |= 0x00000002 | 0x00000040  # FILE_ADD_FILE | FILE_DELETE_CHILD
            child = _win_open_relative(
                current_handle,
                component,
                desired_access=access,
                share_access=0x00000001,
                disposition=1,
                create_options=0x00000001 | 0x00200000 | 0x00000020,
            )
            handles.append(child)
            current_handle = child
            info = _win_file_info(child)
            if info.FileAttributes & _REPARSE_ATTRIBUTE or not (
                info.FileAttributes & 0x00000010
            ):
                raise RecordingUploadError("recording_upload_root_unavailable", 503)
        if not components:
            raise RecordingUploadError("recording_upload_validation_unavailable", 503)
        path_stat = os.stat(root, follow_symlinks=False)
        identity = _root_identity(path_stat)
        info = _win_file_info(current_handle)
        initial_native_identity = (
            info.VolumeSerialNumber,
            (info.FileIndexHigh << 32) | info.FileIndexLow,
        )
        if identity[1] != initial_native_identity[1]:
            raise RecordingUploadError("recording_upload_root_changed", 503)
        if expected_identity is not None and identity != expected_identity:
            raise RecordingUploadError("recording_upload_root_changed", 503)
        marker_stream = _win_ensure_root_marker(current_handle)
        checked = _win_file_info(current_handle)
        checked_identity = (
            checked.VolumeSerialNumber,
            (checked.FileIndexHigh << 32) | checked.FileIndexLow,
        )
        if (
            checked.FileAttributes & _REPARSE_ATTRIBUTE
            or checked_identity != initial_native_identity
        ):
            raise RecordingUploadError("recording_upload_root_changed", 503)

        parent_handle = handles[-2]
        replacement_handle = _win_open_relative(
            parent_handle,
            components[-1],
            desired_access=0x00000001 | 0x00000020 | 0x00000080 | 0x00100000,
            share_access=0x00000001 | 0x00000002,
            disposition=1,
            create_options=0x00000001 | 0x00200000 | 0x00000020,
        )
        handles.append(replacement_handle)
        replacement_info = _win_file_info(replacement_handle)
        replacement_identity = (
            replacement_info.VolumeSerialNumber,
            (replacement_info.FileIndexHigh << 32) | replacement_info.FileIndexLow,
        )
        if (
            replacement_info.FileAttributes & _REPARSE_ATTRIBUTE
            or replacement_identity != initial_native_identity
        ):
            raise RecordingUploadError("recording_upload_root_changed", 503)
        if not api["close_handle"](current_handle):
            raise OSError(api["ctypes"].get_last_error(), "root handle close failed")
        handles.remove(current_handle)
        current_handle = replacement_handle
        volume_path = _win_final_volume_path(current_handle)
        return _WindowsPinnedRoot(
            handles, current_handle, identity, volume_path, marker_stream
        )
    except Exception:
        if marker_stream is not None and not marker_stream.closed:
            marker_stream.close()
        for handle in reversed(handles):
            api["close_handle"](handle)
        raise
