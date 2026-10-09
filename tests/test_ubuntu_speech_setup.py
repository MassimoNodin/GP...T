from __future__ import annotations

from pathlib import Path

import pytest

from f1_engineer.analysis.local_speech import LocalSpeechRuntime
from scripts.setup_ubuntu_speech_runtime import download_model, restore_generated_version, write_pin


def test_pin_matches_runtime_verifier_and_is_repeatable(tmp_path: Path) -> None:
    executable = tmp_path / "whisper-cli"
    executable.write_bytes(b"fixture runtime")
    model = tmp_path / "model.bin"
    model.write_bytes(b"fixture model")
    pin = tmp_path / "pin.json"
    runtime_id = write_pin(pin, executable, model, False)
    assert LocalSpeechRuntime(pin, tmp_path / "audio").status()["status"] == "ready"
    assert write_pin(pin, executable, model, False) == runtime_id


def test_changed_pin_requires_explicit_acceptance(tmp_path: Path) -> None:
    executable = tmp_path / "whisper-cli"
    executable.write_bytes(b"fixture runtime")
    model = tmp_path / "model.bin"
    model.write_bytes(b"fixture model")
    pin = tmp_path / "pin.json"
    previous = write_pin(pin, executable, model, False)
    original = pin.read_bytes()
    executable.write_bytes(b"changed runtime")
    with pytest.raises(RuntimeError, match="Runtime pin changed"):
        write_pin(pin, executable, model, False)
    assert pin.read_bytes() == original
    assert write_pin(pin, executable, model, True) != previous


def test_invalid_bundle_does_not_publish_pin(tmp_path: Path) -> None:
    executable = tmp_path / "whisper-cli"
    executable.write_bytes(b"fixture runtime")
    (tmp_path / "unexpected.dll").write_bytes(b"untracked dependency")
    model = tmp_path / "model.bin"
    model.write_bytes(b"fixture model")
    pin = tmp_path / "pin.json"
    with pytest.raises(RuntimeError, match="runtime_dependency_set_changed"):
        write_pin(pin, executable, model, False)
    assert not pin.exists()


def test_wrong_existing_model_is_preserved(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(b"wrong model")
    with pytest.raises(RuntimeError, match="pinned checksum"):
        download_model(model)
    assert model.read_bytes() == b"wrong model"


def test_only_expected_generated_version_is_restored(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    original = b'{"version": "1.9.3", "name": "whisper.cpp"}'
    package.write_bytes(original.replace(b'"1.9.3"', b'"1.9.3-dev"'))
    restore_generated_version(package, original)
    assert package.read_bytes() == original
    restore_generated_version(package, original)


def test_unexpected_generated_changes_are_preserved(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_bytes(b"unexpected edits")
    with pytest.raises(RuntimeError, match="preserved for review"):
        restore_generated_version(package, b'{"version": "1.9.3"}')
    assert package.read_bytes() == b"unexpected edits"
