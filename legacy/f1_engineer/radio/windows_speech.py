from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from ..errors import F1EngineerError


SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$speech = $null
try {
    Add-Type -AssemblyName System.Speech
    $speech = [System.Speech.Synthesis.SpeechSynthesizer]::new()
    $null = Register-ObjectEvent -InputObject $speech -EventName SpeakCompleted -SourceIdentifier RadioSpeech
    $voices = @($speech.GetInstalledVoices() | Where-Object Enabled | ForEach-Object { $_.VoiceInfo.Name })
    [Console]::WriteLine((@{ok=$true; voices=$voices} | ConvertTo-Json -Compress))
    $prompt = $null
    $completed = $true
    while (($line = [Console]::ReadLine()) -ne $null) {
        try {
            $command = $line | ConvertFrom-Json
            switch ($command.op) {
                'configure' {
                    if ($command.voice) {
                        if ($voices -notcontains $command.voice) { throw 'voice unavailable' }
                        $speech.SelectVoice($command.voice)
                    }
                    $speech.Rate = [int]$command.rate
                    $speech.Volume = [int]$command.volume
                }
                'speak' {
                    if (($command.text -isnot [string]) -or $command.text.Length -gt 1024) { throw 'invalid text' }
                    $speech.SetOutputToDefaultAudioDevice()
                    $prompt = $speech.SpeakAsync($command.text)
                    $completed = $false
                }
                'status' {
                    $failed = $false
                    foreach ($event in @(Get-Event -SourceIdentifier RadioSpeech -ErrorAction SilentlyContinue)) {
                        if ($event.SourceEventArgs.Prompt -eq $prompt) {
                            $completed = $true
                            if ($null -ne $event.SourceEventArgs.Error) { $failed = $true }
                        }
                        Remove-Event -EventIdentifier $event.EventIdentifier
                    }
                    if ($failed) { throw 'speech failed' }
                }
                'cancel' { $speech.SpeakAsyncCancelAll(); $prompt = $null; $completed = $true }
                default { throw 'invalid command' }
            }
            $reply = @{ok=$true; busy=($null -ne $prompt -and -not $completed); voice=$speech.Voice.Name}
        } catch { $reply = @{ok=$false; reason='windows_speech_command_failed'} }
        [Console]::WriteLine(($reply | ConvertTo-Json -Compress))
    }
} catch {
    [Console]::WriteLine((@{ok=$false; reason='windows_speech_unavailable'} | ConvertTo-Json -Compress))
} finally {
    Unregister-Event -SourceIdentifier RadioSpeech -ErrorAction SilentlyContinue
    if ($null -ne $speech) { $speech.Dispose() }
}
"""


class WindowsSpeechUnavailable(F1EngineerError):
    pass


class WindowsSpeech:
    def __init__(self, *, voice: str | None = None, rate: int = 0, volume: int = 75) -> None:
        if sys.platform != "win32":
            raise WindowsSpeechUnavailable("windows_speech_requires_windows")
        if not -10 <= rate <= 10 or not 0 <= volume <= 100:
            raise ValueError("invalid speech settings")
        powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        encoded = base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")
        self._replies: queue.Queue = queue.Queue(maxsize=8)
        self._lock = threading.Lock()
        self._process = subprocess.Popen(
            [str(powershell), "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._reader = threading.Thread(target=self._read_lines, name="radio-speech", daemon=True)
        self._reader.start()
        try:
            ready = self._reply()
            self.voices = ready.get("voices", [])
            if not isinstance(self.voices, list) or not self.voices:
                raise WindowsSpeechUnavailable("windows_voices_unavailable")
            if voice is not None and voice not in self.voices:
                raise WindowsSpeechUnavailable("windows_voice_not_installed")
            configured = self._request({"op": "configure", "voice": voice, "rate": rate, "volume": volume})
            self.voice = configured.get("voice")
        except Exception:
            self.close()
            raise

    def _read_lines(self) -> None:
        while True:
            line = self._process.stdout.readline(16_385)
            try:
                self._replies.put_nowait(line)
            except queue.Full:
                return
            if not line:
                return

    def _reply(self) -> dict:
        try:
            line = self._replies.get(timeout=5)
            if not line or len(line) > 16_384:
                raise ValueError("invalid reply")
            reply = json.loads(line)
            if not isinstance(reply, dict) or reply.get("ok") is not True:
                raise ValueError("failed reply")
            return reply
        except (queue.Empty, ValueError, RecursionError) as exception:
            raise WindowsSpeechUnavailable("windows_speech_transport_failed") from exception

    def _request(self, command: dict) -> dict:
        with self._lock:
            try:
                self._process.stdin.write(json.dumps(command, ensure_ascii=True) + "\n")
                self._process.stdin.flush()
                return self._reply()
            except (OSError, WindowsSpeechUnavailable) as exception:
                self.close()
                raise WindowsSpeechUnavailable("windows_speech_transport_failed") from exception

    def start(self, text: str) -> None:
        if not text.strip() or len(text.encode("utf-8")) > 1024:
            raise ValueError("invalid speech text")
        self._request({"op": "speak", "text": text})

    def busy(self) -> bool:
        reply = self._request({"op": "status"})
        if type(reply.get("busy")) is not bool:
            self.close()
            raise WindowsSpeechUnavailable("windows_speech_status_invalid")
        return reply["busy"]

    def cancel(self) -> None:
        self._request({"op": "cancel"})

    def close(self) -> None:
        if self._process.stdin is not None and not self._process.stdin.closed:
            try:
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        self._reader.join(timeout=1)
        self._process.stdout.close()
