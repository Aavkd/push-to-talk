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
from .logsetup import get_logger

if TYPE_CHECKING:  # pragma: no cover
    import numpy as np

_log = get_logger("transcription")

# Modèle de repli CPU le plus léger (décision arrêtée de la roadmap : on part du
# plus sûr — ``tiny`` — quitte à le réajuster après mesure). Utilisé seulement
# quand le chargement GPU échoue entièrement.
_CPU_FALLBACK_MODEL = "tiny"


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
        # Type de repli effectivement appliqué au chargement, pour que l'UI
        # (toast systray) informe précisément : None (config demandée honorée),
        # "vram" (float16 → int8_float16 sur GPU faute de mémoire), ou "cpu"
        # (bascule complète GPU → CPU avec modèle léger).
        self._fallback: str | None = None

    # ------------------------------------------------------------------ #
    # Chargement du modèle
    # ------------------------------------------------------------------ #
    def _fallback_chain(self) -> list[tuple[str, str, str, str | None]]:
        """Liste ordonnée de candidats ``(modèle, device, compute_type, repli)``.

        On tente d'abord exactement ce que demande la config. En GPU, on prévoit
        deux replis successifs (roadmap Phase 7) :

        - **VRAM** : ``float16`` qui échoue faute de mémoire → ``int8_float16``
          (même modèle, toujours sur GPU) ;
        - **GPU → CPU** : si le GPU reste inutilisable → modèle léger ``tiny`` en
          ``int8`` sur CPU, par sécurité.
        """
        model = self.config.modele
        device = self.config.device
        compute = self.config.compute_type

        chain: list[tuple[str, str, str, str | None]] = [(model, device, compute, None)]
        if device == "cuda":
            if compute == "float16":
                chain.append((model, "cuda", "int8_float16", "vram"))
            chain.append((_CPU_FALLBACK_MODEL, "cpu", "int8", "cpu"))
        return chain

    def load(self) -> None:
        """Charge le modèle Whisper en mémoire (coûteux ; appelé au démarrage).

        Parcourt la chaîne de repli (:meth:`_fallback_chain`) : config demandée,
        puis repli VRAM (``int8_float16``), puis repli CPU (modèle léger). Le
        repli effectivement appliqué est exposé via :attr:`fallback` pour que le
        systray notifie l'utilisateur. Lève si **aucun** backend ne charge.
        """
        # Rend cublas/cudnn trouvables sur Windows avant tout import natif.
        from ._cuda import ensure_cuda_dll_path

        ensure_cuda_dll_path()

        from faster_whisper import WhisperModel

        from . import paths

        # Packagé (Phase 8) : dirige le cache modèle vers le dossier de données
        # utilisateur ; en dev, ``None`` conserve le cache Hugging Face habituel.
        cache_dir = paths.model_cache_dir()
        cache_kwargs = {"download_root": str(cache_dir)} if cache_dir is not None else {}

        last_exc: Exception | None = None
        for model_name, device, compute_type, reason in self._fallback_chain():
            t0 = time.perf_counter()
            try:
                self._model = WhisperModel(
                    model_name, device=device, compute_type=compute_type, **cache_kwargs
                )
            except Exception as exc:  # noqa: BLE001 - on tente le candidat suivant
                last_exc = exc
                _log.warning(
                    "Chargement échoué (%s / %s / %s) : %s",
                    model_name, device, compute_type, exc,
                )
                print(
                    f"[transcription] Échec du chargement "
                    f"{model_name}/{device}/{compute_type} : {exc!s}"
                )
                continue

            self._loaded_with = (model_name, device, compute_type)
            self._fallback = reason
            elapsed = time.perf_counter() - t0
            suffixe = {
                "vram": " (repli VRAM : int8_float16)",
                "cpu": " (repli CPU : GPU indisponible)",
            }.get(reason or "", "")
            _log.info(
                "Modèle '%s' chargé sur %s (%s) en %.1fs%s",
                model_name, device, compute_type, elapsed, suffixe,
            )
            print(
                f"[transcription] Modèle '{model_name}' chargé sur {device} "
                f"({compute_type}) en {elapsed:.1f}s.{suffixe}"
            )
            return

        # Tous les candidats ont échoué : on ne peut pas transcrire.
        _log.error("Aucun backend de transcription disponible : %s", last_exc)
        raise RuntimeError(
            f"Impossible de charger un modèle de transcription "
            f"(dernier échec : {last_exc!s})."
        ) from last_exc

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def loaded_with(self) -> tuple[str, str, str] | None:
        """(modèle, device, compute_type) effectivement utilisés."""
        return self._loaded_with

    @property
    def fallback(self) -> str | None:
        """Repli appliqué au chargement : ``None``, ``"vram"`` ou ``"cpu"``."""
        return self._fallback

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
