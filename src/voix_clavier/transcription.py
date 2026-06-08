"""Transcription locale via faster-whisper (CTranslate2).

Règle d'or de la spec : le modèle est chargé **une seule fois** et conservé en
mémoire ; il ne doit jamais être rechargé à chaque dictée.

En Phase 0, on valide le chemin GPU (``device="cuda"``, ``compute_type="float16"``)
sur un fichier ``.wav`` français. Le repli CPU/VRAM complet est traité en Phase 7,
mais un repli minimal est déjà prévu ici pour ne pas planter si CUDA manque.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np


@dataclass
class TranscriptionResult:
    """Résultat d'une transcription."""

    text: str
    language: str
    language_probability: float
    duration_audio: float       # durée de l'audio en secondes
    elapsed: float              # temps de transcription en secondes


class Transcriber:
    """Encapsule un modèle faster-whisper chargé une fois pour toutes."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()
        self._model = None  # type: ignore[var-annotated]
        self._loaded_with: tuple[str, str, str] | None = None

    # ------------------------------------------------------------------ #
    # Chargement du modèle
    # ------------------------------------------------------------------ #
    def load(self) -> None:
        """Charge le modèle Whisper en mémoire (coûteux ; appelé au démarrage).

        Tente la config demandée (par défaut GPU/float16). En cas d'échec lié
        à CUDA, effectue un repli minimal CPU/int8 et le journalise — le repli
        complet (toasts, modèle léger) est l'affaire de la Phase 7.
        """
        # Rend cublas/cudnn trouvables sur Windows avant tout import natif.
        from ._cuda import ensure_cuda_dll_path

        ensure_cuda_dll_path()

        from faster_whisper import WhisperModel

        device = self.config.device
        compute_type = self.config.compute_type
        model_name = self.config.modele

        t0 = time.perf_counter()
        try:
            self._model = WhisperModel(
                model_name, device=device, compute_type=compute_type
            )
        except Exception as exc:  # noqa: BLE001 - repli volontairement large
            if device == "cuda":
                print(
                    f"[transcription] Échec du chargement GPU ({exc!s}).\n"
                    f"[transcription] Repli temporaire sur CPU (Phase 7 gérera "
                    f"un repli propre avec modèle léger)."
                )
                device, compute_type = "cpu", "int8"
                self._model = WhisperModel(
                    model_name, device=device, compute_type=compute_type
                )
            else:
                raise

        self._loaded_with = (model_name, device, compute_type)
        elapsed = time.perf_counter() - t0
        print(
            f"[transcription] Modèle '{model_name}' chargé sur {device} "
            f"({compute_type}) en {elapsed:.1f}s."
        )

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def loaded_with(self) -> tuple[str, str, str] | None:
        """(modèle, device, compute_type) effectivement utilisés."""
        return self._loaded_with

    # ------------------------------------------------------------------ #
    # Transcription
    # ------------------------------------------------------------------ #
    def transcribe(self, audio: "str | Path | np.ndarray") -> TranscriptionResult:
        """Transcrit un fichier ``.wav`` (chemin) ou un buffer numpy 16 kHz mono.

        Le buffer numpy sera la voie nominale dès la Phase 1 (capture micro) ;
        le chemin fichier sert à la validation Phase 0.
        """
        if self._model is None:
            raise RuntimeError(
                "Modèle non chargé : appelez Transcriber.load() au démarrage."
            )

        source: object = audio
        if isinstance(audio, (str, Path)):
            source = str(audio)

        t0 = time.perf_counter()
        segments, info = self._model.transcribe(
            source,
            language=self.config.langue_whisper,
            beam_size=5,
            vad_filter=True,  # ignore les silences ; utile pour les dictées
        )
        # faster-whisper est paresseux : l'itération déclenche le calcul réel.
        text = "".join(segment.text for segment in segments).strip()
        elapsed = time.perf_counter() - t0

        return TranscriptionResult(
            text=text,
            language=info.language,
            language_probability=info.language_probability,
            duration_audio=info.duration,
            elapsed=elapsed,
        )
