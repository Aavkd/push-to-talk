"""Capture micro (Phase 1).

Enregistre le micro dans un buffer numpy ``float32`` mono à 16 kHz, format
directement exploitable par :mod:`voix_clavier.transcription` (faster-whisper
accepte un ``np.ndarray`` mono normalisé).

La capture est non bloquante : :class:`Recorder` ouvre un ``sounddevice.InputStream``
et accumule les blocs reçus dans un callback. ``start()`` / ``stop()`` encadrent
une dictée ; ``stop()`` renvoie le buffer concaténé.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from .config import Config

SAMPLE_RATE = 16_000  # Hz — format attendu par Whisper
CHANNELS = 1          # mono


def _resolve_device(peripherique: str) -> int | str | None:
    """Traduit le réglage de config en argument ``device`` pour sounddevice.

    - ``""`` → ``None`` (périphérique d'entrée par défaut de Windows) ;
    - chaîne numérique → index entier ;
    - sinon → nom (sounddevice fait une correspondance par sous-chaîne).
    """
    p = (peripherique or "").strip()
    if not p:
        return None
    if p.isdigit():
        return int(p)
    return p


def list_input_devices() -> list[tuple[int, str]]:
    """Liste les périphériques d'entrée (index, nom) — utile au diagnostic."""
    devices = sd.query_devices()
    return [
        (idx, dev["name"])
        for idx, dev in enumerate(devices)
        if dev["max_input_channels"] > 0
    ]


class Recorder:
    """Capture micro non bloquante dans un buffer numpy 16 kHz mono."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._stream: sd.InputStream | None = None
        self._frames: list[np.ndarray] = []
        self._recording = False
        # Niveau sonore instantané (RMS du dernier bloc, ~0.0 → 1.0). Lu par la
        # pilule (Phase 5) pour animer la waveform en temps réel. L'affectation
        # d'un float est atomique en CPython : pas de verrou nécessaire pour une
        # lecture best-effort depuis le thread UI.
        self._level = 0.0

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def level(self) -> float:
        """Niveau sonore instantané (RMS du dernier bloc capté), 0.0 au repos."""
        return self._level if self._recording else 0.0

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        # Appelé depuis le thread audio de PortAudio : rester léger, pas d'I/O.
        if status:
            # Surdébit / perte de blocs : on le note sans interrompre la capture.
            print(f"[audio] statut flux : {status}")
        self._frames.append(indata.copy())
        # RMS du bloc pour le retour visuel temps réel de la pilule (Phase 5).
        self._level = float(np.sqrt(np.mean(np.square(indata))))

    def start(self) -> None:
        """Démarre l'enregistrement (idempotent si déjà en cours)."""
        if self._recording:
            return
        self._frames = []
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            device=_resolve_device(self.config.peripherique),
            callback=self._callback,
        )
        self._stream.start()
        self._recording = True

    def stop(self) -> np.ndarray:
        """Arrête l'enregistrement et renvoie le buffer mono 1D ``float32``.

        Renvoie un tableau vide si rien n'a été capté (utile pour détecter un
        silence / une dictée nulle côté appelant).
        """
        if not self._recording:
            return np.zeros(0, dtype=np.float32)

        assert self._stream is not None
        self._stream.stop()
        self._stream.close()
        self._stream = None
        self._recording = False
        self._level = 0.0

        if not self._frames:
            return np.zeros(0, dtype=np.float32)

        audio = np.concatenate(self._frames, axis=0)
        self._frames = []

        # Aplatir en mono 1D (le flux est déjà mono, mais on reste défensif).
        if audio.ndim > 1:
            audio = audio[:, 0] if audio.shape[1] == 1 else audio.mean(axis=1)
        return np.ascontiguousarray(audio, dtype=np.float32)

    def abort(self) -> None:
        """Interrompt et jette la capture en cours (sans renvoyer de buffer)."""
        if self._stream is not None:
            self._stream.abort()
            self._stream.close()
            self._stream = None
        self._frames = []
        self._recording = False
        self._level = 0.0
