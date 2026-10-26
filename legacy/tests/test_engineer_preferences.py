from __future__ import annotations

import asyncio
import json
import os
from copy import deepcopy

import httpx
import pytest

from f1_engineer.analysis import engineer_ask_service as ask_service
from f1_engineer.analysis import session_engineer as engineer
from f1_engineer.analysis.ai_admission import EngineerAIGate
from f1_engineer.analysis.engineer_ask import EngineerAskRoute, answer_message, select_report_facts
from f1_engineer.analysis.engineer_preferences import (
    DEFAULT_PREFERENCES,
    PREFERENCES_FILENAME,
    EngineerPreferences,
    EngineerPreferencesStore,
    PreferencesInvalid,
    PreferencesUnreadable,
    RevisionConflict,
    answer_style,
    compose_message,
    enabled_topics,
)
from f1_engineer.analysis.session_answers import braking_observation_message
from f1_engineer.api.app import create_app

from .test_session_engineer import Provider, Runtime, SESSION, attempt

TOKEN = "t" * 40
URL = "/api/v1/engineer/preferences"


def document(**changes):
    value = DEFAULT_PREFERENCES.to_dict()
    value.update(changes)
    return value


# ------------------------------------------------------------------ schema


def test_defaults_match_the_settings_screen():
    assert DEFAULT_PREFERENCES.to_dict() == {
        "topics": {
            "tyre_management": True, "track_conditions": True, "race_strategy": True,
            "driving_technique": True, "gap_information": True, "rule_reminders": True,
            "setup_suggestions": False, "general_information": False,
        },
        "verbosity": "balanced",
        "interruption": "allow",
        "depth": "standard",
        "interpretations": True,
        "focus_areas": {
            "driving_consistency": True, "comparative_analysis": True, "corner_analysis": True,
            "setup_impact": True, "tyre_performance": True, "data_quality_notes": False,
        },
    }


def test_round_trip_and_ordering_are_stable():
    value = document(verbosity="detailed", depth="in_depth", interruption="queue")
    assert EngineerPreferences.from_dict(value).to_dict() == value


@pytest.mark.parametrize("mutate, reason, field", [
    (lambda v: v.update(extra=1), "preferences_unknown_key", "extra"),
    (lambda v: v.pop("depth"), "preferences_missing_key", "depth"),
    (lambda v: v["topics"].update(nope=True), "preferences_unknown_key", "topics.nope"),
    (lambda v: v["topics"].pop("race_strategy"), "preferences_missing_key", "topics.race_strategy"),
    (lambda v: v["topics"].update(race_strategy=1), "preferences_invalid_type", "topics.race_strategy"),
    (lambda v: v["focus_areas"].update(setup_impact="yes"), "preferences_invalid_type", "focus_areas.setup_impact"),
    (lambda v: v.update(verbosity="Balanced"), "preferences_invalid_value", "verbosity"),
    (lambda v: v.update(interruption="Allow interruptions"), "preferences_invalid_value", "interruption"),
    (lambda v: v.update(depth="deep"), "preferences_invalid_value", "depth"),
    (lambda v: v.update(interpretations="true"), "preferences_invalid_type", "interpretations"),
    (lambda v: v.update(topics=[]), "preferences_invalid_type", "topics"),
])
def test_validation_is_strict(mutate, reason, field):
    value = deepcopy(document())
    mutate(value)
    with pytest.raises(PreferencesInvalid) as error:
        EngineerPreferences.from_dict(value)
    assert (error.value.reason, error.value.field) == (reason, field)


def test_non_object_is_rejected():
    for bad in (None, [], "x", 3):
        with pytest.raises(PreferencesInvalid):
            EngineerPreferences.from_dict(bad)


# ------------------------------------------------------------------- store


def test_store_defaults_then_atomic_save_and_revision(tmp_path):
    store = EngineerPreferencesStore.beside_database(tmp_path / "db.sqlite3")
    assert store.path == tmp_path.resolve() / PREFERENCES_FILENAME
    assert store.load().revision == 0 and store.current() == DEFAULT_PREFERENCES
    changed = EngineerPreferences.from_dict(document(verbosity="concise"))
    first = store.update(changed)
    assert first.revision == 1 and store.load().preferences == changed
    assert store.update(changed).revision == 1  # identical save is a no-op
    second = store.update(DEFAULT_PREFERENCES, expected_revision=1)
    assert second.revision == 2
    assert [item.name for item in tmp_path.iterdir()] == [PREFERENCES_FILENAME]  # no temp leftovers
    stored = json.loads(store.path.read_text("utf-8"))
    assert stored["schema_version"] == 1 and stored["revision"] == 2


def test_store_revision_conflict(tmp_path):
    store = EngineerPreferencesStore(tmp_path / PREFERENCES_FILENAME)
    store.update(DEFAULT_PREFERENCES.__class__.from_dict(document(depth="quick")))
    with pytest.raises(RevisionConflict) as error:
        store.update(DEFAULT_PREFERENCES, expected_revision=0)
    assert error.value.current == 1
    assert store.load().preferences.depth == "quick"


def test_failed_write_keeps_previous_file_and_cleans_temp(tmp_path, monkeypatch):
    store = EngineerPreferencesStore(tmp_path / PREFERENCES_FILENAME)
    store.update(EngineerPreferences.from_dict(document(depth="quick")))
    before = store.path.read_bytes()

    def broken(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        store.update(EngineerPreferences.from_dict(document(depth="in_depth")))
    monkeypatch.undo()
    assert store.path.read_bytes() == before
    assert [item.name for item in tmp_path.iterdir()] == [PREFERENCES_FILENAME]


@pytest.mark.parametrize("content", [
    b"not json", b"[]", b'{"schema_version":1}', b"\xff\xfe",
    json.dumps({"schema_version": 2, "revision": 1, **document()}).encode(),
    json.dumps({"schema_version": 1, "revision": 0, **document()}).encode(),
    json.dumps({"schema_version": 1, "revision": 1, "x": 1, **document()}).encode(),
    json.dumps({"schema_version": 1, "revision": 1, **document(verbosity="loud")}).encode(),
    b" " * (20 * 1024),
])
def test_corrupt_file_is_unreadable_but_answers_fall_back_and_save_repairs(tmp_path, content):
    path = tmp_path / PREFERENCES_FILENAME
    path.write_bytes(content)
    store = EngineerPreferencesStore(path)
    with pytest.raises(PreferencesUnreadable):
        store.load()
    assert store.current() == DEFAULT_PREFERENCES
    with pytest.raises(RevisionConflict):
        store.update(DEFAULT_PREFERENCES, expected_revision=3)
    assert store.update(DEFAULT_PREFERENCES).revision == 1
    assert store.load().revision == 1


# ------------------------------------------------------------- application


def test_enabled_topics_default_and_mapping():
    assert enabled_topics(DEFAULT_PREFERENCES) == {"tyres", "track_limits", "pit", "lap_time", "gap", "telemetry"}
    assert enabled_topics(None) == enabled_topics(DEFAULT_PREFERENCES)
    everything = EngineerPreferences.from_dict(document(
        topics={key: True for key in DEFAULT_PREFERENCES.to_dict()["topics"]}))
    assert enabled_topics(everything) == {"tyres", "track_limits", "pit", "lap_time", "gap",
                                          "telemetry", "general"}
    nothing = EngineerPreferences.from_dict(document(
        topics={key: False for key in DEFAULT_PREFERENCES.to_dict()["topics"]}))
    assert enabled_topics(nothing) == {"telemetry"}
    only_rules = deepcopy(document())
    only_rules["topics"] = {key: key == "rule_reminders" for key in only_rules["topics"]}
    assert enabled_topics(EngineerPreferences.from_dict(only_rules)) == {"track_limits", "telemetry"}


def test_answer_style_by_preference():
    base = answer_style(DEFAULT_PREFERENCES)
    assert (base["max_facts"], base["max_observations"], base["include_notes"],
            base["include_context_note"], base["include_interpretations"]) == (6, 3, True, False, True)
    concise = answer_style(EngineerPreferences.from_dict(document(verbosity="concise", depth="quick")))
    assert (concise["max_facts"], concise["max_observations"], concise["include_notes"]) == (3, 1, False)
    assert concise["max_chars"] < base["max_chars"] < answer_style(
        EngineerPreferences.from_dict(document(verbosity="detailed")))["max_chars"]
    detailed = answer_style(EngineerPreferences.from_dict(document(verbosity="detailed", depth="in_depth")))
    assert detailed["include_context_note"] and detailed["max_facts"] == 8
    assert answer_style(EngineerPreferences.from_dict(document(verbosity="concise", depth="in_depth")))["include_notes"]
    assert answer_style(EngineerPreferences.from_dict(document(interpretations=False)))["include_interpretations"] is False


def test_compose_message_keeps_required_and_budgets_optional():
    parts = [("A.", True), ("opt1.", False), ("B.", True), ("opt2.", False)]
    assert compose_message(parts, None) == "A. opt1. B. opt2."
    assert compose_message(parts, {"max_sentences": 3, "max_chars": 100}) == "A. opt1. B."
    assert compose_message(parts, {"max_sentences": 1, "max_chars": 1}) == "A. B."
    assert compose_message(parts, {"max_sentences": 9, "max_chars": 9}) == "A. B."


# ------------------------------------------------- analysis-layer integration


@pytest.fixture(autouse=True)
def isolated_gate(monkeypatch):
    monkeypatch.setattr(engineer, "PROCESS_ENGINEER_AI_GATE", EngineerAIGate())


def ask(provider, **kwargs):
    return asyncio.run(engineer.answer_session_question(provider, SESSION, "target", "Compare laps", **kwargs))


def styled(**changes):
    return answer_style(EngineerPreferences.from_dict(document(**changes)))


def test_session_pair_message_unchanged_by_default_style():
    provider = Provider(attempt("target"), attempt("reference"))
    plain = ask(provider)
    assert ask(provider, style=answer_style(DEFAULT_PREFERENCES)) == plain
    assert ask(provider, style=None) == plain


def test_session_pair_message_detail_and_notes():
    provider = Provider(attempt("target"), attempt("reference"))
    plain = ask(provider)["message"]
    detailed = ask(provider, style=styled(verbosity="detailed"))["message"]
    assert detailed.startswith(plain) and "Compared target target with reference reference." in detailed
    concise = ask(provider, style=styled(verbosity="concise"))
    assert "300 ms slower" in concise["message"] and "not coaching" in concise["message"]
    assert concise["diagnostic_only"] is True and concise["coaching_eligible"] is False


def test_pair_qualifications_follow_notes():
    provider = Provider(attempt("target"), attempt("reference"))
    base = ask(provider)
    quals = {"lap_time_difference_ms": 5.0, "qualifications": ["alpha", "beta"]}
    assert engineer._message(quals) == (
        "The target lap is 5 ms slower than the reference lap. This is a diagnostic measurement, "
        "not coaching, ranking, or a causal explanation. Qualifications: alpha, beta.")
    assert "Qualifications" not in engineer._message(quals, styled(verbosity="concise"))
    assert "Qualifications" in engineer._message(quals, styled(verbosity="balanced"))
    assert base["status"] == "available"


BRAKING_REPORT = {"braking_zones": [
    {"supported": True, "region_m": [100, 200], "target_minus_reference_m": -5,
     "difference_interval_m": [-8, -2]},
    {"supported": True, "region_m": [300, 400], "target_minus_reference_m": 4,
     "difference_interval_m": [1, 6]},
]}


def test_braking_message_options():
    default = braking_observation_message(BRAKING_REPORT)
    assert default.count("brake onset") == 2 and "not automatically better" in default
    assert braking_observation_message(BRAKING_REPORT, include_interpretations=False).count("brake onset") == 2
    assert "not automatically better" not in braking_observation_message(
        BRAKING_REPORT, include_interpretations=False)
    one = braking_observation_message(BRAKING_REPORT, max_observations=1)
    assert one.count("brake onset") == 1
    assert braking_observation_message(BRAKING_REPORT, max_observations=0) is None


def test_session_braking_message_respects_style(monkeypatch):
    from f1_engineer.analysis import session_engineer

    def fake_analyze(*_args, **_kwargs):
        return {"status": "available", "lap_time_difference_ms": 300.0, "qualifications": ["q"],
                **BRAKING_REPORT}

    monkeypatch.setattr(session_engineer, "analyze_session_pair", fake_analyze)

    class BrakingRuntime(Runtime):
        async def route_question(self, question, **kwargs):
            route, model = await super().route_question(question, **kwargs)
            return EngineerAskRoute("region_comparison", "braking"), model

    provider = Provider(attempt("target"), attempt("reference"))
    default = ask(provider, runtime=BrakingRuntime())["message"]
    assert ask(provider, runtime=BrakingRuntime(), style=answer_style(DEFAULT_PREFERENCES))["message"] == default
    assert default.count("brake onset") == 2 and "not automatically better" in default
    quick = ask(provider, runtime=BrakingRuntime(),
                style=styled(verbosity="concise", depth="quick", interpretations=False))["message"]
    assert quick.count("brake onset") == 1 and "not automatically better" not in quick
    assert "not coaching" in quick and "Qualifications" not in quick
    no_interp = ask(provider, runtime=BrakingRuntime(), style=styled(interpretations=False))["message"]
    assert no_interp.count("brake onset") == 2 and "not automatically better" not in no_interp
    assert "Qualifications: q." in no_interp


def test_ask_service_style_helpers():
    report = {"intent": "attempt_summary", "facts": [{"kind": "recorded_lap_time"}] * 10}
    assert len(select_report_facts(report, "overview")) == 6
    assert len(select_report_facts(report, "overview", limit=3)) == 3
    assert len(select_report_facts(report, "lap_time", limit=8)) == 8
    route = EngineerAskRoute("attempt_summary", "overview")
    assert len(ask_service._focused_report(report, route)["facts"]) == 6
    assert len(ask_service._focused_report(report, route, styled(depth="quick"))["facts"]) == 3
    focused = ask_service._focused_report(report, route, styled(depth="in_depth"))
    assert len(focused["facts"]) == 8 and focused["omitted_fact_count"] == 2


def test_ask_service_message_shaping():
    route = EngineerAskRoute("attempt_summary", "lap_time")
    report = {"facts": [{"kind": "recorded_lap_time"}], "omitted_fact_count": 2}
    default = ask_service._report_answer({}, route, {}, report)["message"]
    assert ask_service._report_answer({}, route, {}, report, answer_style(DEFAULT_PREFERENCES))["message"] == default
    detailed = ask_service._report_answer({}, route, {}, report, styled(verbosity="detailed"))["message"]
    assert detailed.startswith(default) and "Showing 1 recorded fact(s)" in detailed and "2 other" in detailed
    assert answer_message("unsupported", "overview", facts_available=False) == answer_message(
        "unsupported", "overview", facts_available=False, style=answer_style(DEFAULT_PREFERENCES))
    short = answer_message("unsupported", "overview", facts_available=False, style=styled(verbosity="concise"))
    assert len(short) < len(answer_message("unsupported", "overview", facts_available=False))


# --------------------------------------------------------------------- API


def call(app, method, headers=None, *, content=None, json_body=None):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, URL, headers=headers, content=content, json=json_body)

    return asyncio.run(run())


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "db.sqlite3", control_token=TOKEN, automatic_acquisition=False)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_get_returns_v1_envelope_with_defaults(app):
    response = call(app, "GET")
    assert response.status_code == 200 and response.headers["etag"] == '"0"'
    assert response.json() == {
        "api_version": "v1", "status": "ok", "reason": None,
        "data": {"schema_version": 1, **DEFAULT_PREFERENCES.to_dict(), "revision": 0},
    }


def test_put_requires_token(app):
    assert call(app, "PUT", json_body=document()).status_code == 403
    assert call(app, "PUT", {"Authorization": "Bearer wrong"}, json_body=document()).status_code == 403
    assert call(app, "GET").json()["data"]["revision"] == 0


def test_put_saves_and_get_reads_back(app, tmp_path):
    body = document(verbosity="detailed", depth="in_depth", interpretations=False)
    body["topics"]["setup_suggestions"] = True
    response = call(app, "PUT", AUTH, json_body=body)
    assert response.status_code == 200 and response.headers["etag"] == '"1"'
    assert response.json()["data"] == {"schema_version": 1, **body, "revision": 1}
    assert call(app, "GET").json()["data"] == {"schema_version": 1, **body, "revision": 1}
    assert (tmp_path.resolve() / PREFERENCES_FILENAME).exists()


def test_if_match_concurrency(app):
    assert call(app, "PUT", {**AUTH, "If-Match": '"0"'}, json_body=document(depth="quick")).status_code == 200
    stale = call(app, "PUT", {**AUTH, "If-Match": '"0"'}, json_body=document(depth="standard"))
    assert stale.status_code == 409
    assert stale.json() == {"api_version": "v1", "status": "unavailable",
                            "data": {"revision": 1}, "reason": "preferences_revision_conflict"}
    assert call(app, "GET").json()["data"]["depth"] == "quick"
    assert call(app, "PUT", {**AUTH, "If-Match": "1"}, json_body=document(depth="standard")).status_code == 200
    assert call(app, "PUT", {**AUTH, "If-Match": "*"}, json_body=document(depth="quick")).status_code == 200
    bad = call(app, "PUT", {**AUTH, "If-Match": "abc"}, json_body=document())
    assert bad.status_code == 422 and bad.json()["reason"] == "preferences_if_match_invalid"


@pytest.mark.parametrize("body, reason", [
    (document(verbosity="loud"), "preferences_invalid_value"),
    ({**document(), "revision": 1}, "preferences_unknown_key"),
    ({k: v for k, v in document().items() if k != "topics"}, "preferences_missing_key"),
])
def test_put_rejects_invalid_body_with_422(app, body, reason):
    response = call(app, "PUT", AUTH, json_body=body)
    assert response.status_code == 422 and response.json()["reason"] == reason
    assert call(app, "GET").json()["data"]["revision"] == 0


def test_put_malformed_and_oversized_bodies(app):
    malformed = call(app, "PUT", AUTH, content=b"{nope")
    assert malformed.status_code == 422 and malformed.json()["reason"] == "preferences_body_malformed"
    big = call(app, "PUT", AUTH, content=b'{"x":"' + b"a" * 9000 + b'"}')
    assert big.status_code == 413 and big.json()["reason"] == "preferences_request_too_large"


def test_get_reports_unreadable_file(app, tmp_path):
    (tmp_path.resolve() / PREFERENCES_FILENAME).write_bytes(b"garbage")
    response = call(app, "GET")
    assert response.status_code == 503 and response.json()["status"] == "unavailable"
    assert call(app, "PUT", AUTH, json_body=document()).status_code == 200


def test_require_auth_protects_get_too(tmp_path):
    app = create_app(tmp_path / "db.sqlite3", control_token=TOKEN, require_auth=True,
                     automatic_acquisition=False)
    assert call(app, "GET").status_code == 403
    assert call(app, "GET", AUTH).status_code == 200
