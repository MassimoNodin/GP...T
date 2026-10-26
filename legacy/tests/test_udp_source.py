import asyncio
import socket
import sys

import pytest

from f1_engineer.udp import source
from f1_engineer.udp.source import UDPSource


def _proc_table(*rows):
    return "header\n" + "\n".join(rows) + "\n"


def _row(inode, drops, local="0100007F:515D"):
    return f"0: {local} 00000000:0000 07 00000000:00000000 00:00000000 00000000 0 0 {inode} 2 0x0 {drops}"


def test_drop_count_matches_inode_and_returns_socket_specific_count(tmp_path):
    table = tmp_path / "udp"
    table.write_text(_proc_table(_row(100, 7), _row(200, 0)))
    assert source._socket_drop_count(table, 100) == 7
    assert source._socket_drop_count(table, 200) == 0
    assert source._socket_drop_count(table, 300) is None


def test_unrelated_malformed_rows_do_not_hide_matching_socket(tmp_path):
    table = tmp_path / "udp"
    table.write_text(_proc_table("short malformed row", _row("bad", 0), _row(100, 7)))
    assert source._socket_drop_count(table, 100) == 7


def test_drop_count_handles_ipv4_and_ipv6_tables(tmp_path):
    for filename, inode in (("udp", 11), ("udp6", 22)):
        table = tmp_path / filename
        table.write_text(_proc_table(_row(inode, 4)))
        assert source._socket_drop_count(table, inode) == 4


def test_drop_count_returns_unknown_for_unavailable_or_malformed_proc(tmp_path):
    assert source._socket_drop_count(tmp_path / "missing", 1) is None
    table = tmp_path / "udp"
    for content in ("", "header\n", "header\nshort row\n", "header\n" + _row("bad", 0)):
        table.write_text(content)
        assert source._socket_drop_count(table, 1) is None
    table.write_text("header\n" + ("x" * 4096) + "\n")
    assert source._socket_drop_count(table, 1) is None


def test_socket_inode_reads_fd_link(monkeypatch):
    monkeypatch.setattr(source.os, "readlink", lambda path: "socket:[789]")
    assert source._socket_inode(9) == 789


def test_drop_count_reports_unknown_when_socket_exceeds_scan_budget(tmp_path):
    table = tmp_path / "udp"
    table.write_text(_proc_table(*(_row(inode, 0) for inode in range(4097))))
    assert source._socket_drop_count(table, 4096) is None


def test_kernel_drop_property_is_unknown_off_linux(monkeypatch):
    source_instance = UDPSource()
    source_instance._transport = _Transport(socket.AF_INET)
    monkeypatch.setattr(source.sys, "platform", "win32")
    monkeypatch.setattr(source.time, "monotonic", lambda: 1.0)
    assert source_instance.kernel_receive_drops is None


def test_kernel_drop_property_is_cached_and_retained_after_close(monkeypatch):
    source_instance = UDPSource()
    source_instance._transport = _Transport(socket.AF_INET)
    clock = [1.0]
    sampled = []
    monkeypatch.setattr(source.sys, "platform", "linux")
    monkeypatch.setattr(source.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(source, "_socket_inode", lambda fd: 123)
    monkeypatch.setattr(source, "_socket_drop_count", lambda path, inode: sampled.append((path.name, inode)) or 5)
    assert source_instance.kernel_receive_drops == 5
    clock[0] += 0.1
    assert source_instance.kernel_receive_drops == 5
    assert sampled == [("udp", 123)]
    source_instance.close()
    assert source_instance.kernel_receive_drops == 5


def test_kernel_drop_failure_clears_previous_observation(monkeypatch):
    source_instance = UDPSource()
    source_instance._transport = _Transport(socket.AF_INET)
    clock = [1.0]
    monkeypatch.setattr(source.sys, "platform", "linux")
    monkeypatch.setattr(source.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(source, "_socket_inode", lambda fd: 123)
    samples = iter((5, None))
    monkeypatch.setattr(source, "_socket_drop_count", lambda path, inode: next(samples))
    assert source_instance.kernel_receive_drops == 5
    clock[0] += 0.25
    assert source_instance.kernel_receive_drops is None


def test_open_resets_drop_observation_for_new_socket_lifetime():
    async def exercise():
        source_instance = UDPSource(host="127.0.0.1", port=0)
        source_instance._kernel_drops = 8
        source_instance._kernel_drops_sampled_at = 10.0
        await source_instance.open()
        assert source_instance._kernel_drops is None
        assert source_instance._kernel_drops_sampled_at is None
        source_instance.close()
    import asyncio

    asyncio.run(exercise())


class _Transport:
    def __init__(self, family):
        self.family = family
        self.closed = False

    def get_extra_info(self, name):
        return _Socket(self.family) if name == "socket" else None

    def close(self):
        self.closed = True


class _Socket:
    def __init__(self, family):
        self.family = family

    def fileno(self):
        return 9


def test_kernel_drop_property_uses_ipv6_table(monkeypatch):
    source_instance = UDPSource()
    source_instance._transport = _Transport(socket.AF_INET6)
    monkeypatch.setattr(source.sys, "platform", "linux")
    monkeypatch.setattr(source.time, "monotonic", lambda: 1.0)
    monkeypatch.setattr(source, "_socket_inode", lambda fd: 456)
    sampled = []
    monkeypatch.setattr(source, "_socket_drop_count", lambda path, inode: sampled.append(path.name) or 2)
    assert source_instance.kernel_receive_drops == 2
    assert sampled == ["udp6"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux per-socket procfs diagnostic")
def test_real_linux_socket_attributes_receive_buffer_overflow():
    async def exercise():
        source_instance = UDPSource(host="127.0.0.1", port=0, queue_size=64)
        await source_instance.open()
        try:
            if source_instance.kernel_receive_drops is None:
                pytest.skip("per-socket Linux procfs observations unavailable")
            assert source_instance.kernel_receive_drops == 0
            address = source_instance._transport.get_extra_info("sockname")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                for _ in range(5000):
                    sender.sendto(b"x" * 1024, address)
            await asyncio.sleep(0.3)
            assert source_instance.kernel_receive_drops > 0
            assert source_instance.stats.received > 0
        finally:
            source_instance.close()

    asyncio.run(exercise())
