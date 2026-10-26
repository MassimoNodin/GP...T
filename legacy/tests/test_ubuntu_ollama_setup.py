import json
from pathlib import Path

import pytest

from scripts.setup_ubuntu_ollama import pin_model, service_text, systemd_quote


def test_private_service_has_explicit_limits_and_loopback() -> None:
    unit = service_text(Path('/home/example/runtime/bin/ollama'), Path('/home/example/models'))
    assert 'OLLAMA_HOST=127.0.0.1:11435' in unit
    assert 'OLLAMA_NO_CLOUD=1' in unit
    assert 'OLLAMA_NUM_PARALLEL=1' in unit
    assert 'NoNewPrivileges=true' in unit
    assert 'UMask=0077' in unit


def test_systemd_quote_escapes_path_metacharacters() -> None:
    assert systemd_quote('a% b"c') == '"a%% b\\"c"'
    with pytest.raises(ValueError):
        systemd_quote('line\nbreak')


def test_model_pin_preserved_until_update_accepted(tmp_path: Path) -> None:
    pin = tmp_path / 'pin.json'
    model = {'name': 'qwen3:4b', 'digest': 'a' * 64}
    pin_model(pin, model, False)
    assert json.loads(pin.read_text())['digest'] == 'sha256:' + 'a' * 64
    pin_model(pin, model, False)
    original = pin.read_bytes()
    model['digest'] = 'b' * 64
    with pytest.raises(RuntimeError, match='Model pin changed'):
        pin_model(pin, model, False)
    assert pin.read_bytes() == original
    pin_model(pin, model, True)
    assert json.loads(pin.read_text())['digest'] == 'sha256:' + 'b' * 64


@pytest.mark.parametrize('model', [
    {'name': 'qwen3:4b', 'digest': 'a' * 64, 'remote_host': 'example.com'},
    {'name': 'other', 'digest': 'a' * 64},
    {'name': 'qwen3:4b', 'digest': 'wrong'},
])
def test_rejected_models_do_not_create_pin(tmp_path: Path, model: dict) -> None:
    pin = tmp_path / 'pin.json'
    with pytest.raises(RuntimeError):
        pin_model(pin, model, False)
    assert not pin.exists()
