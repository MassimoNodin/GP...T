from __future__ import annotations

import asyncio
import threading
import time
from copy import deepcopy

import pytest

from f1_engineer.analysis import session_engineer as engineer
from f1_engineer.analysis.ai_admission import EngineerAIGate
from f1_engineer.analysis.engineer_ask import EngineerAskRoute
from f1_engineer.analysis.ollama_runtime import OllamaUnavailable
from f1_engineer.processing.detail_demand import DetailCommand, DetailDemandQueue
from f1_engineer.processing.evidence import EvidenceUnavailable, digest, encode


SESSION = "b" * 32
BINDING = "c" * 64


def attempt(identifier, *, state="published", driver="driver-1", role="player"):
    return {
        "id": identifier, "original_id": identifier, "session": SESSION, "driver": driver, "role": role,
        "binding_verified": True, "rows": 61,
        "readiness": {"state": state},
        "manifest": {"driver": driver, "epoch": 1, "format": 2026, "qualifications": [],
                     "start_frame": 10, "end_frame": 70, "source_start": 21, "source_end": 100},
        "payload": {
            "disposition": "completed", "lap_time_ms": 3300 if identifier == "target" else 3000,
            "game_valid": True, "start_observed": True, "pit_encountered": False,
            "car_index": 7 if role == "opponent" else 0, "sample_count": 61,
            "context_segments": [{"from_frame_identifier": 0, "context": {
                "packet_format": 2026, "session_type": "short_practice",
                "game_mode": "driver_career_25", "rule_set": "practice_qualifying",
                "track_id": 5, "track_name": "Austria", "track_length_m": 4318,
                "formula_id": 0, "equal_car_performance_id": 0,
                "steering_assist_id": 0, "braking_assist_id": 0, "gearbox_assist_id": 1,
            }}],
        },
    }


class Provider:
    def __init__(self, *metadata):
        self.data = {item["id"]: deepcopy(item) for item in metadata}
        self.reads = []

    def attempt(self, identifier, *, session=None):
        self.reads.append(identifier)
        if identifier not in self.data or self.data[identifier]["session"] != session:
            raise EvidenceUnavailable("attempt_not_in_session")
        return deepcopy(self.data[identifier])

    def attempts(self, session, *, limit=100, after=""):
        return [deepcopy(item) for identifier, item in sorted(self.data.items())
                if identifier > after and item["session"] == session][:limit]

    def evidence(self, identifier, *, session=None):
        metadata = self.attempt(identifier, session=session)
        if metadata["readiness"]["state"] != "published":
            raise EvidenceUnavailable("trace_not_selected")
        rows = [{"frame_identifier": index, "lap_distance_m": index * 5,
                 "current_lap_time_ms": index * (60 if identifier == "target" else 50),
                 "session_time_s": 10 + index * .06, "speed_mps": 40,
                 "throttle": .5, "brake": .4 if 12 <= index < 25 else 0,
                 "steering": 0, "gear": 4, "drs_active": False} for index in range(61)]
        return metadata, rows


class Materializer:
    def __init__(self, provider):
        self.provider = provider
        self.calls = []

    def materialize(self, identifier, *, session):
        self.calls.append((identifier, session, threading.get_ident()))
        metadata = self.provider.attempt(identifier, session=session)
        metadata["readiness"] = {"state": "published"}
        metadata["detail"] = {"profile": "historical-detail-v1", "state": "available"}
        metadata["materialization"] = {"source_revision": identifier, "source_session": session,
                                       "source_manifest_hash": digest(encode(metadata["manifest"])),
                                       "derived_revision": "temporary-" + identifier}
        published = Provider(metadata)
        return metadata, published.evidence(identifier, session=session)[1]


class Runtime:
    def __init__(self, *, error=None, route="region_comparison"):
        self.calls = []
        self.error = error
        self.route = route

    async def route_question(self, question, **kwargs):
        assert engineer.PROCESS_ENGINEER_AI_GATE.try_acquire() is None
        self.calls.append((question, kwargs))
        if self.error:
            raise self.error
        return EngineerAskRoute(self.route, "overview"), {"model_name": "fake", "claim": "ignore me"}


class LiveOwner:
    def __init__(self, provider, *, available=True, publish=None):
        self.provider = provider
        self.available = available
        self.publish = publish
        self.queue = DetailDemandQueue()
        self.queue.publish_targets(SESSION, ({"binding_id": BINDING, "car_index": 7,
                                             "epoch": 1, "packet_format": 2026,
                                             "is_player": False},))
        self.commands = []
        self.polls = 0

    def detail_targets(self, session):
        return self.queue.targets(session)

    def enqueue_detail_command(self, command):
        self.commands.append(command)
        return self.queue.enqueue(command)

    def detail_task(self, task_id):
        self.polls += 1
        snapshot = self.queue.get(task_id)
        if snapshot.state == "queued":
            command = self.queue.pop()
            assert command.task_id == task_id
            snapshot = self.queue.apply(command, effective_sequence=20, target_current=True,
                                        frame_ordinal=10)
            if self.publish:
                self.publish(self.provider)
            if self.available:
                self.queue.mark_available(task_id, BINDING)
        return self.queue.get(task_id)


@pytest.fixture
def provider():
    return Provider(attempt("target"), attempt("reference"))


@pytest.fixture(autouse=True)
def isolated_gate(monkeypatch):
    monkeypatch.setattr(engineer, "PROCESS_ENGINEER_AI_GATE", EngineerAIGate())


def ask(provider, **kwargs):
    return asyncio.run(engineer.answer_session_question(provider, SESSION, "target", "Compare laps", **kwargs))


def test_real_siblings_measure_before_model_and_preserve_factual_answer(provider):
    deterministic = ask(provider)
    runtime = Runtime()
    routed = ask(provider, runtime=runtime)
    assert deterministic["status"] == routed["status"] == "available"
    assert deterministic["message"] == routed["message"]
    assert "300 ms slower" in routed["message"] and "ignore me" not in routed["message"]
    assert routed["report"]["lap_time_difference_ms"] == 300
    assert routed["selection"]["reference"] == "reference"
    assert runtime.calls == [("Compare laps", {"selection_kind": "pair",
                                             "allowed_routes": ("region_comparison",)})]
    assert routed["diagnostic_only"] is True
    assert routed["coaching_eligible"] is routed["ranking_eligible"] is False
    lease = engineer.PROCESS_ENGINEER_AI_GATE.try_acquire()
    assert lease is not None
    lease.close()


@pytest.mark.parametrize("question", ["", " ", "x" * 2001, None])
def test_invalid_question_never_reads_or_routes(provider, question):
    runtime = Runtime()
    result = asyncio.run(engineer.answer_session_question(provider, SESSION, "target", question, runtime=runtime))
    assert result["status"] == "unsupported"
    assert not provider.reads and not runtime.calls


@pytest.mark.parametrize("target,binding", [(None, None), ("target", BINDING), ("", None)])
def test_revision_and_future_modes_are_exclusive(provider, target, binding):
    result = asyncio.run(engineer.answer_session_question(provider, SESSION, target, "Compare",
                                                          binding_id=binding))
    assert result["reason"] == "request_target_invalid"
    assert not provider.reads


def test_missing_reference_and_invalid_explicit_reference_do_not_route(provider):
    runtime = Runtime()
    del provider.data["reference"]
    assert ask(provider, runtime=runtime)["reason"] == "no_eligible_reference"
    provider.data["reference"] = attempt("reference")
    assert ask(provider, reference_id="missing", runtime=runtime)["status"] == "unavailable"
    assert not runtime.calls


def test_deferred_requested_pair_requires_opt_in_and_uses_bounded_copied_overlay(provider):
    for item in provider.data.values():
        item["readiness"]["state"] = "deferred"
    original = deepcopy(provider.data)
    materializer = Materializer(provider)
    runtime = Runtime()
    assert ask(provider, reference_id="reference", materializer=materializer,
               runtime=runtime)["reason"] == "historical_materialization_not_authorized"
    assert not materializer.calls and not runtime.calls
    result = ask(provider, reference_id="reference", materializer=materializer,
                 allow_materialization=True, runtime=runtime)
    assert result["status"] == "available"
    assert [item[0] for item in materializer.calls] == ["target", "reference"]
    assert all(item[2] != threading.get_ident() for item in materializer.calls)
    assert len(result["provenance"]["materializations"]) == 2
    assert result["report"]["target"]["revision"] == "target"
    assert provider.data == original


def test_automatic_reference_scan_does_not_materialize_deferred_candidates(provider):
    provider.data["reference"]["readiness"]["state"] = "deferred"
    materializer = Materializer(provider)
    result = ask(provider, materializer=materializer, allow_materialization=True)
    assert result["reason"] == "no_eligible_reference"
    assert materializer.calls == []


@pytest.mark.parametrize("error", [EvidenceUnavailable("measurement_failed"), OllamaUnavailable("runtime_offline")])
def test_stable_evidence_and_model_failures(provider, monkeypatch, error):
    runtime = Runtime(error=error if isinstance(error, OllamaUnavailable) else None)
    if isinstance(error, EvidenceUnavailable):
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr(engineer, "analyze_session_pair", fail)
    result = ask(provider, runtime=runtime)
    assert result["status"] == "unavailable" and result["reason"] == str(error)
    assert len(runtime.calls) == (1 if isinstance(error, OllamaUnavailable) else 0)


def test_ai_busy_and_unsupported_intent(provider):
    lease = engineer.PROCESS_ENGINEER_AI_GATE.try_acquire()
    runtime = Runtime()
    try:
        assert ask(provider, runtime=runtime)["reason"] == "engineer_ai_busy"
        assert not runtime.calls
    finally:
        lease.close()
    assert ask(provider, runtime=Runtime(route="unsupported"))["status"] == "unsupported"


def future_ask(provider, live, **kwargs):
    return asyncio.run(engineer.answer_session_question(provider, SESSION, None, "Next lap",
                                                        binding_id=BINDING, live_runtime=live, **kwargs))


def publish_future(provider):
    provider.data["future"] = attempt("future", driver=BINDING, role="opponent")


def test_future_summary_is_new_boundary_verified_and_never_routes(provider):
    provider.data["old"] = attempt("old", driver=BINDING, role="opponent")
    runtime = Runtime()
    live = LiveOwner(provider, publish=publish_future)
    result = future_ask(provider, live, runtime=runtime)
    assert result["status"] == "available"
    assert result["selection"]["target"] == "future"
    assert result["report"]["kind"] == "attempt_summary"
    assert result["message"].startswith("Complete observed lap evidence is available")
    assert result["provenance"]["origin"] == "future_binding"
    detail = result["provenance"]["detail"]
    assert detail["activation_frame_ordinal"] == 10 and detail["effective_sequence"] == 20
    assert detail["cleanup"] == {"status": "queued", "reason": None}
    assert [command.command for command in live.commands] == ["post", "release"]
    assert live.commands[0].task_id == live.commands[1].task_id
    assert not runtime.calls


@pytest.mark.parametrize("field,value", [("start_frame", 9), ("source_start", 20),
                                         ("source_start", None), ("epoch", 2)])
def test_future_rejects_pre_activation_or_other_epoch_rows(provider, field, value):
    def publish(base):
        publish_future(base)
        base.data["future"]["manifest"][field] = value
    runtime = Runtime()
    result = future_ask(provider, LiveOwner(provider, publish=publish), runtime=runtime)
    assert result["status"] == "pending" and result["reason"] == "future_trace_not_published"
    assert not runtime.calls


def test_future_chooses_deterministic_revision_and_cannot_compare_nonplayer(provider):
    def publish(base):
        for identifier in ("future-z", "future-a"):
            base.data[identifier] = attempt(identifier, driver=BINDING, role="opponent")
    assert future_ask(provider, LiveOwner(provider, publish=publish))["selection"]["target"] == "future-a"
    provider = Provider(attempt("reference"))
    runtime = Runtime()
    result = future_ask(provider, LiveOwner(provider, publish=publish),
                        reference_id="reference", runtime=runtime)
    assert result["reason"] == "target_invalid"
    assert not runtime.calls


def test_active_without_coverage_has_bounded_wait_without_renewal(provider, monkeypatch):
    clock = [0.0]
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        await asyncio.sleep(0)
    monkeypatch.setattr(engineer, "_clock", lambda: clock[0])
    monkeypatch.setattr(engineer, "_sleep", sleep)
    live = LiveOwner(provider, available=False)
    runtime = Runtime()
    result = future_ask(provider, live, runtime=runtime)
    assert result["status"] == "pending" and result["reason"] == "detail_wait_timeout"
    assert clock[0] == pytest.approx(engineer.LIVE_WAIT_S)
    assert max(sleeps) <= .1 and len(sleeps) <= 2401
    assert [command.command for command in live.commands] == ["post", "release"]
    assert not runtime.calls


def test_future_wait_can_cover_a_realistic_lap_duration(provider, monkeypatch):
    clock = [0.0]
    live = LiveOwner(provider, available=False)

    async def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 90.0 and "future" not in provider.data:
            publish_future(provider)
            live.queue.mark_available(live.commands[0].task_id, BINDING)
        await asyncio.sleep(0)

    monkeypatch.setattr(engineer, "_clock", lambda: clock[0])
    monkeypatch.setattr(engineer, "_sleep", sleep)
    result = future_ask(provider, live)
    assert result["status"] == "available"
    assert result["selection"]["target"] == "future"
    assert clock[0] >= 90.0
    assert [command.command for command in live.commands] == ["post", "release"]


def test_future_current_target_must_be_verified_nonplayer(provider):
    live = LiveOwner(provider)
    live.queue.publish_targets(SESSION, ({"binding_id": BINDING, "is_player": True},))
    assert future_ask(provider, live)["reason"] == "detail_target_is_player_or_unknown"
    assert not live.commands
    live.queue.clear_targets()
    assert future_ask(provider, live)["reason"] == "detail_target_not_current"


def test_future_scan_overflow_fails_before_leasing(provider):
    provider.data = {f"candidate-{index:04}": attempt(f"candidate-{index:04}") for index in range(513)}
    live = LiveOwner(provider)
    result = future_ask(provider, live)
    assert result["reason"] == "candidate_scan_budget_exceeded"
    assert not live.commands


def test_queue_cleanup_rejection_is_surfaced_without_overwriting_other_requests(provider):
    live = LiveOwner(provider, publish=publish_future)
    original_poll = live.detail_task
    def saturated(task_id):
        snapshot = original_poll(task_id)
        for index in range(16):
            live.queue.enqueue(DetailCommand("post", f"other-{index}", SESSION, BINDING))
        return snapshot
    live.detail_task = saturated
    result = future_ask(provider, live)
    assert result["reason"] == "detail_command_budget_busy"
    assert result["provenance"]["detail"]["cleanup"]["status"] == "rejected"
    assert len(live.commands) == 2
    assert [live.queue.pop().task_id for index in range(16)] == [f"other-{index}" for index in range(16)]


def test_future_overlap_cancellation_releases_only_owned_unique_lease(provider, monkeypatch):
    async def run():
        waiting = asyncio.Event()
        async def sleep(seconds):
            waiting.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(engineer, "_sleep", sleep)
        live = LiveOwner(provider, available=False)
        first = asyncio.create_task(engineer.answer_session_question(
            provider, SESSION, None, "Next lap", binding_id=BINDING, live_runtime=live))
        await asyncio.wait_for(waiting.wait(), 2)
        waiting.clear()
        second = asyncio.create_task(engineer.answer_session_question(
            provider, SESSION, None, "Next lap", binding_id=BINDING, live_runtime=live))
        await asyncio.wait_for(waiting.wait(), 2)
        posts = [command for command in live.commands if command.command == "post"]
        assert len(posts) == 2 and posts[0].task_id != posts[1].task_id
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert live.commands[-1] == DetailCommand("release", posts[0].task_id, SESSION, BINDING)
        assert live.queue.get(posts[1].task_id).state == "active"
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        assert live.commands[-1].task_id == posts[1].task_id
    asyncio.run(run())


def test_cancelled_materialization_keeps_shared_gate_until_worker_finishes(provider):
    provider.data["target"]["readiness"]["state"] = "deferred"
    started = threading.Event()
    finish = threading.Event()
    class BlockingMaterializer(Materializer):
        def materialize(self, identifier, *, session, cancelled=None):
            started.set()
            finish.wait(3)
            assert cancelled()
            return super().materialize(identifier, session=session)
    materializer = BlockingMaterializer(provider)
    async def run():
        request = asyncio.create_task(engineer.answer_session_question(
            provider, SESSION, "target", "Compare", materializer=materializer,
            allow_materialization=True))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            rejected = await engineer.answer_session_question(
                provider, SESSION, "target", "Compare", materializer=materializer,
                allow_materialization=True)
            assert rejected["reason"] == "materialization_busy"
        finally:
            finish.set()
    asyncio.run(run())
    lease = engineer._materializer_gate(materializer).try_acquire()
    assert lease is not None
    lease.close()


def test_request_timeout_has_no_early_model_call(provider, monkeypatch):
    def slow_measure(*args, **kwargs):
        time.sleep(.1)
        return {"lap_time_difference_ms": 300}
    monkeypatch.setattr(engineer, "analyze_session_pair", slow_measure)
    monkeypatch.setattr(engineer, "REQUEST_TIMEOUT_S", .02)
    runtime = Runtime()
    result = ask(provider, runtime=runtime)
    assert result["reason"] == "engineer_request_timeout"
    assert not runtime.calls


def test_overlay_cap_identity_and_copy_isolation(provider):
    overlay = engineer._Overlay(provider)
    materializer = Materializer(provider)
    metadata, rows = materializer.materialize("target", session=SESSION)
    overlay.add(provider.data["target"], (metadata, rows))
    metadata["payload"]["lap_time_ms"] = 0
    rows.clear()
    assert overlay.attempt("target", session=SESSION)["payload"]["lap_time_ms"] == 3300
    assert len(overlay.evidence("target", session=SESSION)[1]) == 61
    with pytest.raises(EvidenceUnavailable, match="not_in_session"):
        overlay.attempt("target", session="other")
    overlay.add(provider.data["reference"], materializer.materialize("reference", session=SESSION))
    with pytest.raises(EvidenceUnavailable, match="overlay_budget"):
        overlay.add(provider.data["target"], (metadata, rows))


@pytest.mark.parametrize("field", ["source_revision", "source_session", "source_manifest_hash"])
def test_overlay_requires_real_source_provenance(provider, field):
    metadata, rows = Materializer(provider).materialize("target", session=SESSION)
    del metadata["materialization"][field]
    with pytest.raises(EvidenceUnavailable, match="identity_mismatch"):
        engineer._Overlay(provider).add(provider.data["target"], (metadata, rows))


@pytest.mark.parametrize("section,field,value", [
    ("materialization", "source_revision", "other"),
    ("materialization", "source_session", "other"),
    ("materialization", "source_manifest_hash", "other"),
    (None, "original_id", "other"), (None, "role", "opponent"),
    (None, "driver", "other"), (None, "id", "other"), (None, "session", "other"),
    ("payload", "lap_time_ms", 1), ("manifest", "epoch", 2),
    ("manifest", "format", 2025), ("manifest", "start_frame", 9),
    ("manifest", "end_frame", 71),
])
def test_materialization_cannot_change_source_identity_or_immutable_fields(provider, section, field, value):
    provider.data["target"]["readiness"]["state"] = "deferred"
    class TamperedMaterializer(Materializer):
        def materialize(self, identifier, *, session):
            metadata, rows = super().materialize(identifier, session=session)
            (metadata if section is None else metadata[section])[field] = value
            return metadata, rows
    runtime = Runtime()
    result = ask(provider, materializer=TamperedMaterializer(provider),
                 allow_materialization=True, runtime=runtime)
    assert result["reason"] == "materialization_identity_mismatch"
    assert not runtime.calls


def test_overlay_trace_budgets_fail_closed(provider, monkeypatch):
    metadata, rows = Materializer(provider).materialize("target", session=SESSION)
    monkeypatch.setattr(engineer, "MAX_READ_ROWS", 60)
    with pytest.raises(EvidenceUnavailable, match="read_budget"):
        engineer._Overlay(provider).add(provider.data["target"], (metadata, rows))
    monkeypatch.setattr(engineer, "MAX_READ_ROWS", 20000)
    monkeypatch.setattr(engineer, "MAX_READ_BYTES", 64)
    with pytest.raises(EvidenceUnavailable, match="read_budget"):
        engineer._Overlay(provider).add(provider.data["target"], (metadata, rows))


def test_cache_is_forwarded_without_caching_selection(provider):
    class Cache:
        def __init__(self):
            self.keys = []
        def get_or_compute(self, key, compute):
            self.keys.append(key)
            return compute()
    cache = Cache()
    result = ask(provider, cache=cache)
    assert result["status"] == "available" and len(cache.keys) == 1
    provider.data["reference"]["payload"]["game_valid"] = False
    assert ask(provider, cache=cache)["reason"] == "no_eligible_reference"
    assert len(cache.keys) == 1


def test_live_outer_timeout_releases_owned_lease(provider, monkeypatch):
    async def run():
        async def sleep(seconds):
            await asyncio.sleep(1)
        monkeypatch.setattr(engineer, "_sleep", sleep)
        monkeypatch.setattr(engineer, "LIVE_REQUEST_TIMEOUT_S", .02)
        live = LiveOwner(provider, available=False)
        result = await engineer.answer_session_question(
            provider, SESSION, None, "Next lap", binding_id=BINDING, live_runtime=live)
        assert result["reason"] == "engineer_request_timeout"
        assert [command.command for command in live.commands] == ["post", "release"]
    asyncio.run(run())
