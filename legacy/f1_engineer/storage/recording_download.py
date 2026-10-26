from __future__ import annotations

import ctypes
import hmac
import os
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable

from .database import Database
from .import_jobs import recording_download_version, recording_root_namespace

DEFAULT_MAX_RECORDING_DOWNLOAD_BYTES = 16 * 1024**3
MAX_RECORDING_DOWNLOAD_CHUNK_BYTES = 64 * 1024
MAX_CONCURRENT_RECORDING_DOWNLOADS = 2


class RecordingDownloadError(RuntimeError):
    def __init__(self, reason: str, status_code: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class RecordingDownloadService:
    def __init__(
        self,
        database_path: str | Path,
        recordings_root: str | Path,
        *,
        max_download_bytes: int = DEFAULT_MAX_RECORDING_DOWNLOAD_BYTES,
        max_concurrent_downloads: int = MAX_CONCURRENT_RECORDING_DOWNLOADS,
    ) -> None:
        if (
            isinstance(max_download_bytes, bool)
            or not isinstance(max_download_bytes, int)
            or not 1 <= max_download_bytes <= DEFAULT_MAX_RECORDING_DOWNLOAD_BYTES
        ):
            raise ValueError("max_download_bytes_invalid")
        if (
            isinstance(max_concurrent_downloads, bool)
            or not isinstance(max_concurrent_downloads, int)
            or max_concurrent_downloads != MAX_CONCURRENT_RECORDING_DOWNLOADS
        ):
            raise ValueError("max_concurrent_downloads_invalid")
        self.database_path = Path(database_path).expanduser().resolve()
        self.recordings_root = Path(recordings_root).expanduser().resolve()
        self._posix_root_identity: tuple[int, int] | None = None
        self._posix_root_lock = threading.Lock()
        if os.name != "nt":
            try:
                root_fd, root_stat = _open_posix_root_directory(self.recordings_root)
            except (OSError, RecordingDownloadError):
                pass
            else:
                try:
                    self._posix_root_identity = _directory_identity(root_stat)
                finally:
                    os.close(root_fd)
        self.max_download_bytes = max_download_bytes
        self._slots = threading.BoundedSemaphore(max_concurrent_downloads)

    def open_download(
        self, capture_id: str, download_version: str
    ) -> OpenRecordingDownload:
        if not self._slots.acquire(blocking=False):
            raise RecordingDownloadError("recording_download_limit_reached", 429)
        try:
            return self._open_download(capture_id, download_version)
        except Exception:
            self._slots.release()
            raise

    def _open_download(
        self, capture_id: str, download_version: str
    ) -> OpenRecordingDownload:
        try:
            root = self.recordings_root
            namespace = recording_root_namespace(root)
            with Database(self.database_path, read_only=True) as db:
                row = db.connection.execute(
                    """SELECT
                           CASE WHEN typeof(relative_path)='text'
                                     AND length(relative_path) BETWEEN 1 AND 4096
                                     AND length(CAST(relative_path AS BLOB)) <= 16384
                                THEN relative_path END AS relative_path,
                           CASE WHEN typeof(display_name)='text'
                                     AND length(display_name) BETWEEN 1 AND 512
                                     AND length(CAST(display_name AS BLOB)) <= 2048
                                THEN display_name END AS display_name,
                           CASE WHEN typeof(byte_size)='integer' AND byte_size >= 0
                                THEN byte_size END AS byte_size,
                           CASE WHEN typeof(modified_ns)='integer' AND modified_ns >= 0
                                THEN modified_ns END AS modified_ns
                         FROM recording_sources
                        WHERE capture_id=? AND root_namespace=?""",
                    (capture_id, namespace),
                ).fetchone()
        except (OSError, ValueError, TypeError) as exc:
            raise RecordingDownloadError(
                "recording_source_unavailable", 404
            ) from exc
        except Exception as exc:
            if isinstance(exc, RecordingDownloadError):
                raise
            raise RecordingDownloadError(
                "recording_download_service_unavailable", 503
            ) from exc

        if row is None:
            raise RecordingDownloadError("recording_source_unavailable", 404)
        relative_path = row["relative_path"]
        display_name = row["display_name"]
        byte_size = row["byte_size"]
        modified_ns = row["modified_ns"]
        if (
            not isinstance(relative_path, str)
            or not isinstance(display_name, str)
            or not isinstance(byte_size, int)
            or isinstance(byte_size, bool)
            or not isinstance(modified_ns, int)
            or isinstance(modified_ns, bool)
            or byte_size < 0
            or modified_ns < 0
        ):
            raise RecordingDownloadError("recording_source_unavailable", 404)
        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or len(relative.parts) != 1
            or relative.name in {"", ".", ".."}
            or relative.name != display_name
            or Path(display_name).is_absolute()
            or Path(display_name).name != display_name
            or relative.suffix.lower() != ".f1ecap"
        ):
            raise RecordingDownloadError("recording_source_unavailable", 404)

        expected_version = recording_download_version(
            namespace,
            capture_id,
            relative_path,
            byte_size,
            modified_ns,
        )
        if not hmac.compare_digest(expected_version, download_version):
            raise RecordingDownloadError("recording_source_changed", 409)

        file_path = root / relative.name
        try:
            if os.name == "nt":
                stream, file_stat = _open_direct_regular_file(root, file_path)
            else:
                with self._posix_root_lock:
                    stream, file_stat, root_identity = _open_posix_file(
                        root,
                        relative.name,
                        self._posix_root_identity,
                    )
                    if self._posix_root_identity is None:
                        self._posix_root_identity = root_identity
        except RecordingDownloadError:
            raise
        except OSError as exc:
            reason = (
                "recording_source_busy"
                if getattr(exc, "winerror", None) in {32, 33}
                or getattr(exc, "errno", None) in {16, 26}
                else "recording_source_unavailable"
            )
            status = 409 if reason == "recording_source_busy" else 404
            raise RecordingDownloadError(reason, status) from exc

        if file_stat.st_size != byte_size or file_stat.st_mtime_ns != modified_ns:
            stream.close()
            raise RecordingDownloadError("recording_source_changed", 409)
        if file_stat.st_size > self.max_download_bytes:
            stream.close()
            raise RecordingDownloadError("recording_download_size_limit", 413)
        return OpenRecordingDownload(
            stream=stream,
            display_name=display_name,
            download_version=expected_version,
            byte_size=file_stat.st_size,
            initial_stat=file_stat,
            release_slot=self._slots.release,
        )


@dataclass
class OpenRecordingDownload:
    stream: BinaryIO
    display_name: str
    download_version: str
    byte_size: int
    initial_stat: os.stat_result
    release_slot: Callable[[], None]
    _closed: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def read_chunk(self) -> bytes:
        chunk = self.stream.read(MAX_RECORDING_DOWNLOAD_CHUNK_BYTES)
        current = os.fstat(self.stream.fileno())
        if not _same_file_observation(self.initial_stat, current):
            raise RecordingDownloadError("recording_source_changed", 409)
        return chunk

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.stream.close()
        finally:
            self.release_slot()


def _open_direct_regular_file(
    root: Path, file_path: Path
) -> tuple[BinaryIO, os.stat_result]:
    if os.name == "nt":
        return _open_windows_file(root, file_path)
    stream, file_stat, _identity = _open_posix_file(root, file_path.name, None)
    return stream, file_stat


def _open_posix_root_directory(root: Path) -> tuple[int, os.stat_result]:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not hasattr(os, flag) for flag in required):
        raise RecordingDownloadError("recording_source_validation_unavailable", 503)
    if not root.is_absolute() or not root.anchor:
        raise RecordingDownloadError("recording_source_validation_unavailable", 503)
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    root_fd = os.open(root.anchor, directory_flags)
    try:
        for component in root.parts[1:]:
            child_fd = os.open(component, directory_flags, dir_fd=root_fd)
            os.close(root_fd)
            root_fd = child_fd
        root_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise RecordingDownloadError("recording_source_unavailable", 404)
        _directory_identity(root_stat)
        return root_fd, root_stat
    except Exception:
        os.close(root_fd)
        raise


def _directory_identity(value: os.stat_result) -> tuple[int, int]:
    if not value.st_ino:
        raise RecordingDownloadError("recording_source_validation_unavailable", 503)
    return value.st_dev, value.st_ino


def _open_posix_file(
    root: Path,
    name: str,
    expected_root_identity: tuple[int, int] | None,
) -> tuple[BinaryIO, os.stat_result, tuple[int, int]]:
    root_fd, root_stat = _open_posix_root_directory(root)
    file_fd: int | None = None
    try:
        root_identity = _directory_identity(root_stat)
        if (
            expected_root_identity is not None
            and root_identity != expected_root_identity
        ):
            raise RecordingDownloadError("recording_source_changed", 409)
        file_flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
        )
        before = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        _require_regular_file(before)
        file_fd = os.open(name, file_flags, dir_fd=root_fd)
        after = os.fstat(file_fd)
        _require_regular_file(after)
        if not _same_file_observation(before, after):
            raise RecordingDownloadError("recording_source_changed", 409)
        stream = os.fdopen(file_fd, "rb", buffering=0)
        file_fd = None
        return stream, after, root_identity
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(root_fd)


def _open_windows_file(root: Path, file_path: Path) -> tuple[BinaryIO, os.stat_result]:
    import msvcrt
    from ctypes import wintypes

    before = file_path.stat(follow_symlinks=False)
    _require_regular_file(before)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
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
    invalid_handle = wintypes.HANDLE(-1).value
    handle = create_file(
        str(file_path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ: deny write and delete sharing
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x08000000,  # OPEN_REPARSE_POINT | SEQUENTIAL_SCAN
        None,
    )
    handle_value = handle if isinstance(handle, int) else handle.value
    if handle_value == invalid_handle:
        error = ctypes.get_last_error()
        raise OSError(error, "CreateFileW failed", str(file_path), error)

    owns_handle = True
    file_fd: int | None = None
    try:
        class FileAttributeTagInfo(ctypes.Structure):
            _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

        info = FileAttributeTagInfo()
        get_info = kernel32.GetFileInformationByHandleEx
        get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        get_info.restype = wintypes.BOOL
        if not get_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "file handle attributes unavailable")
        if info.FileAttributes & 0x00000400 or info.FileAttributes & 0x00000010:
            raise RecordingDownloadError("recording_source_unavailable", 404)

        get_final_path = kernel32.GetFinalPathNameByHandleW
        get_final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        get_final_path.restype = wintypes.DWORD
        required = get_final_path(handle, None, 0, 0)
        if required <= 0 or required > 32768:
            raise OSError(ctypes.get_last_error(), "file handle path unavailable")
        final_path_buffer = ctypes.create_unicode_buffer(required + 1)
        actual = get_final_path(handle, final_path_buffer, len(final_path_buffer), 0)
        if actual <= 0 or actual >= len(final_path_buffer):
            raise OSError(ctypes.get_last_error(), "file handle path unavailable")
        if not _windows_path_is_direct_child(root, final_path_buffer.value):
            raise RecordingDownloadError("recording_source_unavailable", 404)

        handle_number = int(handle_value)
        file_fd = msvcrt.open_osfhandle(handle_number, os.O_RDONLY | os.O_BINARY)
        owns_handle = False
        current = os.fstat(file_fd)
        if not _same_file_observation(before, current):
            raise RecordingDownloadError("recording_source_changed", 409)
        stream = os.fdopen(file_fd, "rb", buffering=0)
        file_fd = None
        return stream, current
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if owns_handle:
            close_handle(handle)


def _windows_path_is_direct_child(root: Path, final_path: str) -> bool:
    import ntpath

    def normalized(value: str) -> str:
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return ntpath.normcase(ntpath.normpath(value)).rstrip("\\")

    normalized_root = normalized(str(root))
    normalized_file = normalized(final_path)
    return ntpath.dirname(normalized_file) == normalized_root


def _require_regular_file(value: os.stat_result) -> None:
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(value, "st_file_attributes", 0)
    if (
        not stat.S_ISREG(value.st_mode)
        or attributes & reparse_attribute
        or value.st_size < 0
        or value.st_mtime_ns < 0
    ):
        raise RecordingDownloadError("recording_source_unavailable", 404)


def _same_file_observation(first: os.stat_result, second: os.stat_result) -> bool:
    if not first.st_ino or not second.st_ino:
        raise RecordingDownloadError("recording_source_validation_unavailable", 503)
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_size == second.st_size
        and first.st_mtime_ns == second.st_mtime_ns
    )
