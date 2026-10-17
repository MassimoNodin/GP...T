from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("failure", [None, "speech.start", "engineer.start", "engineer.close", "live.start"])
def test_api_lifespan_warms_runtime_and_cleans_up_on_failure(monkeypatch, tmp_path, failure):
    module = importlib.import_module("f1_engineer.api.app")
    calls = []

    class Resource:
        def __init__(self, name):
            self.name = name
            self.store = SimpleNamespace()
            self.ask_log = SimpleNamespace(record=lambda category: lambda handler: handler)

        def record(self, action):
            name = f"{self.name}.{action}"
            calls.append(name)
            if name == failure:
                raise RuntimeError(name)

        def start(self):
            self.record("start")

        def close(self):
            self.record("close")

        def stop(self):
            self.record("stop")

        def enable_dispatch(self):
            self.record("enable_dispatch")

        def stop_dispatch(self):
            self.record("stop_dispatch")

        def cleanup_stale_temporary_files(self):
            self.record("cleanup")

        def set_topics(self, topics):
            pass

    class AsyncResource(Resource):
        async def start(self):
            self.record("start")

        async def close(self):
            self.record("close")

    for attribute, name in (
        ("ImportController", "import"), ("RecordingUploadService", "upload"),
        ("RecordingController", "recording"), ("ReplayController", "replay"),
        ("LiveSessionRuntime", "live"), ("build_engineer_activity", "activity"),
    ):
        resource = Resource(name)
        monkeypatch.setattr(module, attribute, lambda *args, resource=resource, **kwargs: resource)
    speech = AsyncResource("speech")
    monkeypatch.setattr(module, "SpeechRouter", lambda *args, **kwargs: speech)
    monkeypatch.setattr(module, "register_speech_routes", lambda *args, **kwargs: None)
    engineer = AsyncResource("engineer")
    app = module.create_app(tmp_path / "archive.sqlite3", engineer_runtime=engineer)

    async def run():
        async with app.router.lifespan_context(app):
            calls.append("yield")
            assert app.state.engineer_runtime is engineer
            assert calls.index("engineer.start") < calls.index("yield")

    if failure is None:
        asyncio.run(run())
    else:
        with pytest.raises(RuntimeError, match=failure):
            asyncio.run(run())
    for name in ("import", "upload", "recording", "replay", "live"):
        assert calls.count(f"{name}.close") == 1
    if failure != "live.start":
        assert calls.count("speech.close") == 1
        assert calls.count("activity.stop") == 1
        assert calls.count("import.stop_dispatch") == 1
    if failure in (None, "engineer.start", "engineer.close"):
        assert calls.count("engineer.close") == 1
        assert calls.index("engineer.close") < calls.index("speech.close")
    if failure in ("speech.start", "engineer.start", "live.start"):
        assert "yield" not in calls
