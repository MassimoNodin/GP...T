"""Apply dashboard configuration at playback/capture boundaries, never restart tasks."""
from __future__ import annotations

import time
from types import SimpleNamespace

from ..analysis.engineer_preferences import EngineerPreferences, PreferencesInvalid
from .settings import RadioSettings

OWNER_TOPICS = {
    "automatic_gaps": "gap_information", "gap_changes": "gap_information",
    "opponent_regions": "gap_information", "lap_time": "driving_technique",
    "pace_sectors": "driving_technique", "lap_reports": "driving_technique",
    "pit_benchmark": "race_strategy", "tyres": "tyre_management",
    "track_limits": "rule_reminders", "weather": "track_conditions",
    "setup": "setup_suggestions",
}


def styled_interpreter(interpreter, limit):
    async def answer(*args, **kwargs):
        result = await interpreter(*args, **kwargs)
        grounded = result.get("grounded")
        if isinstance(grounded, dict) and isinstance(grounded.get("reply"), dict):
            reply = grounded["reply"]
            text = reply.get("answer")
            if isinstance(text, str) and len(text) > limit:
                sentences = text.rsplit(". ", 1)
                suffix = " " + sentences[-1] if len(sentences) > 1 and len(sentences[-1]) < limit // 2 else ""
                shortened = text[:limit - len(suffix) - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "." + suffix
                return {**result, "grounded": {**grounded, "reply": {**reply, "answer": shortened}}}
        return result
    return answer


class LivePlayback:
    def __init__(self, playback, factory, settings, notify):
        self.current = playback
        self.factory = factory
        self.settings = settings
        self.pending = None
        self.notify = notify

    def configure(self, settings):
        self.pending = settings

    def start(self, text):
        if self.pending is not None and not self.current.busy():
            settings, self.pending = self.pending, None
            try:
                replacement = self.factory(SimpleNamespace(**settings))
            except Exception as exc:
                self.notify({"event": "radio_settings_invalid", "reason": type(exc).__name__, "field": "playback"})
            else:
                self.current.close()
                self.current = replacement
                self.settings = settings
        self.current.start(text)

    def busy(self):
        return self.current.busy()

    def cancel(self):
        self.current.cancel()

    def close(self):
        self.current.close()


class RadioLiveSettings:
    def __init__(self, args, scheduler, controller, voice, client, notify):
        self.args, self.scheduler, self.controller, self.voice = args, scheduler, controller, voice
        self.client, self.notify = client, notify
        self.revision = "unapplied"
        self.mode = None
        self.last = {}
        self.devices_revision = 0
        self.refresh_devices = lambda: None

    def apply(self, block, *, received_at=None):
        if block is None:
            return
        started = time.monotonic()
        try:
            if not isinstance(block, dict) or not isinstance(block.get("revision"), str):
                raise ValueError("revision")
            discovery = block.get("devices_revision", 0)
            if type(discovery) is int and discovery >= 0 and discovery != self.devices_revision:
                self.devices_revision = discovery
                self.refresh_devices()
            if block["revision"] == self.revision:
                return
            radio = RadioSettings.from_dict(block["radio"]).to_dict()
            assistance = block["assistance"]
            defaults = EngineerPreferences().to_dict()
            defaults.update(assistance)
            prefs = EngineerPreferences.from_dict(defaults)
            if type(block["radio_revision"]) is not int or block["radio_revision"] < 0:
                raise ValueError("radio_revision")
        except (ValueError, KeyError, TypeError) as exc:
            self.notify({"event": "radio_settings_invalid", "reason": str(exc)[:128]})
            return
        changed = []
        current = prefs.to_dict()
        limit = min({"concise": 420, "balanced": 900, "detailed": 1000}[prefs.verbosity],
                    {"quick": 420, "standard": 900, "in_depth": 1000}[prefs.depth])
        if block["radio_revision"] > 0:
            for key, value in radio.items():
                if getattr(self.args, key, None) != value:
                    changed.append(key)
                setattr(self.args, key, value)
            playback = {key: radio[key] for key in ("tts_engine", "voice", "rate", "volume")}
            if any(key in changed for key in playback):
                self.scheduler.playback.configure(playback)
            self.mode = radio["proactive_mode"]
        interpreter = None
        if self.client is not None and not self.args.dry_run and self.args.intent_mode == "luna":
            interpreter = (styled_interpreter(self.client.answer, limit)
                           if self.args.answer_mode == "grounded" else self.client.interpret)
        self.controller.answer_interpreter = styled_interpreter(self.client.answer, limit) if self.client else None
        self.voice.configure(microphone=self.args.microphone, output=self.args.cue_output,
                             cue_volume=self.args.cue_volume, maximum_seconds=self.args.capture_seconds,
                             interpret=interpreter)
        changed += [key for key in assistance if self.last.get(key) != current[key]]
        self.last = current
        self.scheduler.proactive_topics = dict(prefs.topics)
        self.scheduler.interruption = prefs.interruption
        self.revision = block["revision"]
        self.notify({"event": "radio_settings_applied", "revision": self.revision, "changed": changed,
                     "applied_monotonic_s": time.monotonic(),
                     "delivery_elapsed_s": time.monotonic() - (received_at or started),
                     "apply_elapsed_s": time.monotonic() - started})

    def tick(self):
        if self.mode is not None and self.scheduler.active is None and not self.voice.blocks_playback:
            if self.controller.proactive_mode != self.mode:
                self.controller.set_proactive_mode(self.mode)
            self.mode = None
