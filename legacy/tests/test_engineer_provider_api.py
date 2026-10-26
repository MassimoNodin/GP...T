from fastapi.testclient import TestClient

from f1_engineer.api.app import create_app
from f1_engineer.analysis.codex_runtime import CodexRuntime
from f1_engineer.analysis.ollama_runtime import OllamaRuntime


TOKEN = "engineer-provider-test-token-0123456789abcdef"


def application(tmp_path, **kwargs):
    return create_app(tmp_path / "archive.sqlite3", recordings_root=tmp_path / "recordings",
                      control_token=TOKEN, automatic_acquisition=False, **kwargs)


def test_engineer_defaults_to_exact_subscription_model_without_starting_inference(tmp_path, monkeypatch):
    starts = []

    async def warm_without_inference(runtime):
        starts.append(runtime)

    monkeypatch.setattr(CodexRuntime, "start", warm_without_inference)
    app = application(tmp_path)
    with TestClient(app):
        runtime = app.state.engineer_runtime
        assert isinstance(runtime, CodexRuntime)
        assert runtime.home == (tmp_path / ".codex-engineer").resolve()
        assert starts == [runtime]
        assert not runtime.home.exists()


def test_ollama_requires_explicit_provider_selection(tmp_path):
    app = application(tmp_path, engineer_ai_provider="ollama")
    with TestClient(app):
        assert isinstance(app.state.engineer_runtime, OllamaRuntime)


def test_codex_profile_and_executable_are_server_configured(tmp_path):
    app = application(tmp_path, codex_home=tmp_path / "subscription-profile",
                      codex_executable="/configured/codex")
    with TestClient(app):
        assert app.state.engineer_runtime.home == (tmp_path / "subscription-profile").resolve()
        assert app.state.engineer_runtime.executable == "/configured/codex"
