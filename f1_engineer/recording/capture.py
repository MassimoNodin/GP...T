from __future__ import annotations

import json
import os
import struct
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from ..errors import CaptureFormatError
from ..udp.models import RawDatagram


_MAGIC = b"F1ECAP01"
_FILE_PREFIX = struct.Struct("!I")
_ENTRY_TYPE = struct.Struct("!B")
_RECORD = struct.Struct("!QQQHHI")
_DATAGRAM_ENTRY = 1
_FOOTER_ENTRY = 2
_MAX_METADATA_BYTES = 1_048_576
_MAX_SUMMARY_BYTES = 1_048_576
_MAX_HOST_BYTES = 512
_MAX_PACKET_BYTES = 65_535


def _read_exact(stream: BinaryIO, length: int, *, allow_eof: bool = False) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < length:
        part = stream.read(length - len(chunks))
        if not part:
            if allow_eof and not chunks:
                return None
            raise CaptureFormatError("capture ended in the middle of a record")
        chunks.extend(part)
    return bytes(chunks)


def read_capture_metadata(stream: BinaryIO) -> dict[str, object]:
    """Validate and read only the bounded F1ECAP file header metadata."""
    magic = _read_exact(stream, len(_MAGIC))
    if magic != _MAGIC:
        raise CaptureFormatError("not an F1 Engineer capture or unsupported capture version")
    prefix = _read_exact(stream, _FILE_PREFIX.size)
    assert prefix is not None
    metadata_length = _FILE_PREFIX.unpack(prefix)[0]
    if metadata_length > _MAX_METADATA_BYTES:
        raise CaptureFormatError("capture metadata length exceeds the safety limit")
    metadata_bytes = _read_exact(stream, metadata_length)
    assert metadata_bytes is not None
    metadata = json.loads(metadata_bytes.decode("utf-8"))
    if not isinstance(metadata, dict):
        raise CaptureFormatError("capture metadata must be a JSON object")
    if metadata.get("schema_version") != 1:
        raise CaptureFormatError("unsupported capture schema version")
    return metadata


class CaptureWriter:
    def __init__(
        self,
        path: str | Path,
        metadata: dict[str, object] | None = None,
        *,
        overwrite: bool = False,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            **(metadata or {}),
            "schema_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        encoded = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(encoded) > _MAX_METADATA_BYTES:
            raise ValueError("capture metadata is too large")
        self._stream = self.path.open("wb" if overwrite else "xb")
        self.metadata = document
        try:
            self._stream.write(_MAGIC)
            self._stream.write(_FILE_PREFIX.pack(len(encoded)))
            self._stream.write(encoded)
        except Exception:
            self._stream.close()
            raise
        self._writes = 0
        self._closed = False

    def write(self, datagram: RawDatagram) -> None:
        if self._closed:
            raise ValueError("capture writer is closed")
        host = datagram.source_host.encode("utf-8")
        if len(host) > _MAX_HOST_BYTES:
            raise ValueError("source host is too long to record")
        if len(datagram.payload) > _MAX_PACKET_BYTES:
            raise ValueError("UDP datagram exceeds the maximum UDP payload size")
        self._stream.write(_ENTRY_TYPE.pack(_DATAGRAM_ENTRY))
        self._stream.write(
            _RECORD.pack(
                datagram.sequence,
                datagram.captured_at_ns,
                datagram.monotonic_ns,
                len(host),
                datagram.source_port,
                len(datagram.payload),
            )
        )
        self._stream.write(host)
        self._stream.write(datagram.payload)
        self._writes += 1
        if self._writes % 64 == 0:
            self._stream.flush()

    def close(self, summary: dict[str, object] | None = None) -> None:
        if not self._closed:
            try:
                completion = {"status": "complete", **(summary or {})}
                encoded = json.dumps(
                    completion, separators=(",", ":"), sort_keys=True
                ).encode("utf-8")
                if len(encoded) > _MAX_SUMMARY_BYTES:
                    raise ValueError("capture completion summary is too large")
                self._stream.write(_ENTRY_TYPE.pack(_FOOTER_ENTRY))
                self._stream.write(_FILE_PREFIX.pack(len(encoded)))
                self._stream.write(encoded)
                self._stream.flush()
                os.fsync(self._stream.fileno())
            finally:
                self._stream.close()
                self._closed = True

    def __enter__(self) -> CaptureWriter:
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        if exc_type is None:
            self.close()
        else:
            self.close(
                {
                    "status": "incomplete",
                    "error_type": getattr(exc_type, "__name__", "error"),
                }
            )


class CaptureReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._stream = self.path.open("rb")
        self.completion: dict[str, object] | None = None
        self.complete = False
        try:
            self.metadata = read_capture_metadata(self._stream)
        except Exception:
            self._stream.close()
            raise

    @property
    def bytes_read(self) -> int:
        return self._stream.tell()

    def __iter__(self) -> Iterator[RawDatagram]:
        while True:
            entry_bytes = _read_exact(self._stream, _ENTRY_TYPE.size, allow_eof=True)
            if entry_bytes is None:
                return
            (entry_type,) = _ENTRY_TYPE.unpack(entry_bytes)
            if entry_type == _FOOTER_ENTRY:
                size_bytes = _read_exact(self._stream, _FILE_PREFIX.size)
                assert size_bytes is not None
                summary_length = _FILE_PREFIX.unpack(size_bytes)[0]
                if summary_length > _MAX_SUMMARY_BYTES:
                    raise CaptureFormatError("capture summary length exceeds the safety limit")
                summary_bytes = _read_exact(self._stream, summary_length)
                assert summary_bytes is not None
                summary = json.loads(summary_bytes.decode("utf-8"))
                if not isinstance(summary, dict):
                    raise CaptureFormatError("capture completion summary must be a JSON object")
                if self._stream.read(1):
                    raise CaptureFormatError("capture contains data after its completion footer")
                self.completion = summary
                self.complete = summary.get("status") == "complete"
                return
            if entry_type != _DATAGRAM_ENTRY:
                raise CaptureFormatError(f"unknown capture entry type {entry_type}")
            header_bytes = _read_exact(self._stream, _RECORD.size)
            assert header_bytes is not None
            sequence, captured_at_ns, monotonic_ns, host_length, source_port, payload_length = (
                _RECORD.unpack(header_bytes)
            )
            if host_length > _MAX_HOST_BYTES or payload_length > _MAX_PACKET_BYTES:
                raise CaptureFormatError("capture record exceeds a field size limit")
            host_bytes = _read_exact(self._stream, host_length)
            payload = _read_exact(self._stream, payload_length)
            assert host_bytes is not None and payload is not None
            try:
                host = host_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CaptureFormatError("capture contains an invalid source host") from exc
            yield RawDatagram(
                sequence=sequence,
                captured_at_ns=captured_at_ns,
                monotonic_ns=monotonic_ns,
                source_host=host,
                source_port=source_port,
                payload=payload,
            )

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> CaptureReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
