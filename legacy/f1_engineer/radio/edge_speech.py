from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from pathlib import Path

from ..errors import F1EngineerError

DEFAULT_VOICE = "en-AU-WilliamNeural"


class EdgeSpeechUnavailable(F1EngineerError):
    pass


class EdgeSpeech:
    """Playback backed by the Edge online neural voices; same interface as WindowsSpeech."""

    def __init__(self, *, voice: str | None = None, rate: int = 4, volume: int = 75, fallback=None) -> None:
        if not -10 <= rate <= 10 or not 0 <= volume <= 100:
            raise ValueError("invalid speech settings")
        try:
            import edge_tts
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame
        except ImportError as exception:
            raise EdgeSpeechUnavailable("edge_speech_requires_edge_tts_and_pygame") from exception
        self._edge_tts = edge_tts
        self._fallback = fallback
        self._pygame = pygame
        self.voice = voice or DEFAULT_VOICE
        self._rate = f"{round((3 ** (rate / 10) - 1) * 100):+d}%"
        self._volume = volume / 100
        self._lock = threading.Lock()
        self._generation = 0
        self._active = False
        self._folder = Path(tempfile.mkdtemp(prefix="f1-radio-speech-"))
        try:
            pygame.mixer.init()
        except pygame.error as exception:
            raise EdgeSpeechUnavailable("edge_speech_audio_unavailable") from exception
        self.voices = [self.voice]

    def start(self, text: str) -> None:
        if not text.strip() or len(text.encode("utf-8")) > 1024:
            raise ValueError("invalid speech text")
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._active = True
        threading.Thread(target=self._speak, args=(text, generation), name="radio-edge-speech", daemon=True).start()

    def _current(self, generation: int) -> bool:
        return generation == self._generation

    def _speak(self, text: str, generation: int) -> None:
        path = self._folder / f"{generation}.mp3"
        try:
            asyncio.run(self._edge_tts.Communicate(text, self.voice, rate=self._rate).save(str(path)))
            with self._lock:
                if not self._current(generation):
                    return
                music = self._pygame.mixer.music
                music.load(str(path))
                music.set_volume(self._volume)
                music.play()
            while True:
                with self._lock:
                    if not self._current(generation) or not self._pygame.mixer.music.get_busy():
                        break
                threading.Event().wait(0.05)
        except Exception:
            with self._lock:
                if self._fallback is not None and self._current(generation):
                    try:
                        self._fallback.start(text)
                    except Exception:
                        pass
        finally:
            with self._lock:
                if self._current(generation):
                    self._active = False
                    try:
                        self._pygame.mixer.music.unload()
                    except Exception:
                        pass
            path.unlink(missing_ok=True)

    def busy(self) -> bool:
        with self._lock:
            active = self._active
        return active or (self._fallback is not None and self._fallback.busy())

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
            self._active = False
            if self._fallback is not None:
                self._fallback.cancel()
            try:
                self._pygame.mixer.music.stop()
                self._pygame.mixer.music.unload()
            except Exception:
                pass

    def close(self) -> None:
        self.cancel()
        if self._fallback is not None:
            self._fallback.close()
        try:
            self._pygame.mixer.quit()
        except Exception:
            pass
        for item in self._folder.glob("*"):
            item.unlink(missing_ok=True)
        try:
            self._folder.rmdir()
        except OSError:
            pass
