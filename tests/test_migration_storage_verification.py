from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_migration_storage import (
    MARKER_CONTENT,
    MARKER_NAME,
    SafetyError,
    _unlink_owned,
    _verify_application_pipeline,
    _write_filler,
    main,
    run,
    validate_target,
)


def marked_target(path: Path) -> Path:
    path.mkdir()
    (path / MARKER_NAME).write_text(MARKER_CONTENT, encoding="utf-8")
    return path


@pytest.mark.parametrize("max_bytes", [0, -1, 1024 * 1024 * 1024 + 1])
def test_rejects_invalid_or_oversized_capacity(tmp_path, max_bytes):
    target = marked_target(tmp_path / "target")
    with pytest.raises(SafetyError, match="max-bytes"):
        validate_target(target, isolated=True, max_bytes=max_bytes)


def test_requires_explicit_isolated_flag(tmp_path):
    target = marked_target(tmp_path / "target")
    with pytest.raises(SafetyError, match="--isolated"):
        validate_target(target, isolated=False, max_bytes=1024)


def test_refuses_missing_or_inexact_marker(tmp_path):
    missing = tmp_path / "missing"
    missing.mkdir()
    with pytest.raises(SafetyError, match="exact marker"):
        validate_target(missing, isolated=True, max_bytes=1024)
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / MARKER_NAME).write_text("wrong\n", encoding="utf-8")
    with pytest.raises(SafetyError, match="exact marker"):
        validate_target(wrong, isolated=True, max_bytes=1024)


def test_refuses_nonempty_target_and_symlink(tmp_path):
    target = marked_target(tmp_path / "nonempty")
    (target / "existing.sqlite3").touch()
    with pytest.raises(SafetyError, match="empty except"):
        validate_target(target, isolated=True, max_bytes=1024)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SafetyError, match="symlink"):
        validate_target(link, isolated=True, max_bytes=1024)


def test_refuses_symlink_inside_target(tmp_path):
    target = marked_target(tmp_path / "target")
    (target / "linked").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(SafetyError, match="symlink"):
        validate_target(target, isolated=True, max_bytes=1024)


def test_rejects_filesystem_larger_than_supplied_bound(tmp_path, monkeypatch):
    target = marked_target(tmp_path / "target")
    monkeypatch.setattr("scripts.verify_migration_storage.shutil.disk_usage", lambda _: type("Usage", (), {"total": 2048})())
    with pytest.raises(SafetyError, match="filesystem exceeds"):
        validate_target(target, isolated=True, max_bytes=1024)


def test_accepts_exactly_marked_target_within_bound(tmp_path, monkeypatch):
    target = marked_target(tmp_path / "target")
    monkeypatch.setattr("scripts.verify_migration_storage.shutil.disk_usage", lambda _: type("Usage", (), {"total": 1024})())
    assert validate_target(target, isolated=True, max_bytes=1024) == target.resolve()


def test_filler_collision_does_not_remove_unowned_file(tmp_path, monkeypatch):
    target = marked_target(tmp_path / "target")
    monkeypatch.setattr("scripts.verify_migration_storage.shutil.disk_usage", lambda _: type("Usage", (), {"total": 1024})())
    monkeypatch.setattr("scripts.verify_migration_storage.uuid.uuid4", lambda: type("Nonce", (), {"hex": "collision"})())
    original = __import__("scripts.verify_migration_storage", fromlist=["_write_filler"])._write_filler
    collision = target / "migration-filler-collision.bin"

    def collide(path, limit, owned):
        collision.write_bytes(b"belongs to someone else")
        return original(path, limit, owned)

    monkeypatch.setattr("scripts.verify_migration_storage._write_filler", collide)
    with pytest.raises(FileExistsError):
        run(target, isolated=True, max_bytes=1024)
    assert collision.read_bytes() == b"belongs to someone else"
    assert sorted(item.name for item in target.iterdir()) == [MARKER_NAME, collision.name]


def test_application_pipeline_option_is_forwarded_by_cli(monkeypatch, capsys):
    called = {}

    def fake_run(target, *, isolated, max_bytes, application_pipeline):
        called.update({"target": target, "isolated": isolated, "max_bytes": max_bytes,
                       "application_pipeline": application_pipeline})
        return {"application_pipeline": {"recovered": True}}

    monkeypatch.setattr("scripts.verify_migration_storage.run", fake_run)
    assert main(["disposable", "--isolated", "--max-bytes", "4096", "--application-pipeline"]) == 0
    assert called["application_pipeline"] is True
    assert called["isolated"] is True
    assert called["max_bytes"] == 4096
    assert '"recovered": true' in capsys.readouterr().out


def test_pipeline_sidecar_collision_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.verify_migration_storage.uuid.uuid4", lambda: type("Nonce", (), {"hex": "fixed"})())
    collision = tmp_path / "migration-pipeline-fixed.sqlite3-wal"
    collision.write_bytes(b"preexisting sidecar")
    with pytest.raises(FileExistsError, match="sidecar path collision"):
        _verify_application_pipeline(tmp_path, max_bytes=1024)
    assert collision.read_bytes() == b"preexisting sidecar"
    assert sorted(path.name for path in tmp_path.iterdir()) == [collision.name]


def test_owned_unlink_preserves_replaced_path(tmp_path):
    path = tmp_path / "owned-filler.bin"
    path.write_bytes(b"owned")
    stat = path.stat()
    owned = {path: (stat.st_dev, stat.st_ino)}
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"replacement")
    replacement.replace(path)
    assert _unlink_owned(path, owned) is False
    assert path.read_bytes() == b"replacement"


def test_filler_retries_with_smaller_writes_after_enospc(tmp_path, monkeypatch):
    path = tmp_path / "filler.bin"
    owned = {}
    write_sizes = []
    real_write = __import__("os").write

    def write_with_tail_limit(descriptor, data):
        write_sizes.append(len(data))
        if len(data) > 4096:
            raise OSError(28, "No space left on device")
        return real_write(descriptor, data)

    monkeypatch.setattr("scripts.verify_migration_storage.os.write", write_with_tail_limit)
    assert _write_filler(path, 16 * 1024, owned) == 16 * 1024
    assert path.stat().st_size == 16 * 1024
    assert max(write_sizes) > 4096
    assert min(write_sizes) == 4096
