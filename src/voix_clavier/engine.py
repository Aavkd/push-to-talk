"""Moteur de dictée (Phase 2) : raccourci global + machine à états + modes.

Orchestration sans UI :

  raccourci global ──▶ machine à états ──▶ capture ──▶ transcription ──▶ injection

Deux modes lus depuis la configuration :
  - **push-to-talk** : on enregistre tant que la combinaison est maintenue ;
    le relâchement déclenche transcription + collage.
  - **toggle** : un premier appui démarre l'écoute, un second l'arrête et
    déclenche transcription + collage. Un anti-rebond évite les doubles appuis.

Les callbacks du raccourci arrivent sur le thread d'écoute de ``pynput`` ; la
transcription (coûteuse) est déléguée à un thread *worker* pour ne pas figer
l'écoute du clavier. La machine à états sert de garde-fou contre les
chevauchements : tant qu'une dictée n'est pas revenue à ``Repos``, une nouvelle
activation est ignorée (le durcissement fin de l'enchaînement rapide relève de
la Phase 3).

Le modèle Whisper est chargé **une seule fois** (:meth:`DictationEngine.start`)
et réutilisé pour toutes les dictées, conformément à la spec.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from .audio import Recorder
from .config import Config
from .hotkey import GlobalHotkey
from .injection import inject
from .state import LABELS, IllegalTransition, State, StateMachine
from .transcription import Transcriber

# Intervalle minimal entre deux activations en mode toggle (anti-rebond).
_TOGGLE_DEBOUNCE_S = 0.30


def _beep(kind: str) -> None:
    """Bip système de feedback minimal à chaque transition (best-effort)."""
    try:
        import winsound
    except ImportError:  # pragma: no cover - hors Windows
        return
    freqs = {"start": 880, "stop": 600, "done": 1320, "error": 220}
    try:
        winsound.Beep(freqs.get(kind, 440), 120)
    except RuntimeError:  # pragma: no cover - périphérique audio absent
        pass


class DictationEngine:
    """Pilote une dictée complète à partir du raccourci global."""

    def __init__(
        self,
        config: Config,
        *,
        transcriber: Transcriber | None = None,
        recorder: Recorder | None = None,
        beep: bool = True,
        verbose: bool = True,
        on_mic_error: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.transcriber = transcriber or Transcriber(config)
        self.recorder = recorder or Recorder(config)
        self.machine = StateMachine(on_change=self._on_state_change)
        self._beep_enabled = beep
        self._verbose = verbose
        # Callback optionnel (UI) appelé quand la capture micro échoue ; sert au
        # toast « Erreur micro » du systray (Phase 4). Le moteur reste utilisable
        # sans UI : si None, on se contente du print/bip existants.
        self.on_mic_error = on_mic_error

        self._hotkey: GlobalHotkey | None = None
        # Sérialise les décisions d'activation/désactivation (threads pynput).
        self._gate = threading.Lock()
        self._last_toggle = 0.0
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Charge le modèle (si besoin) et arme le raccourci global."""
        if not self.transcriber.is_loaded:
            self.transcriber.load()
        self._install_hotkey()

    def stop(self) -> None:
        """Désarme le raccourci et interrompt une éventuelle capture en cours."""
        if self._hotkey is not None:
            self._hotkey.stop()
            self._hotkey = None
        self.recorder.abort()

    def _install_hotkey(self) -> None:
        if self._hotkey is not None:
            self._hotkey.stop()
        self._hotkey = GlobalHotkey(
            self.config.raccourci,
            on_activate=self._on_activate,
            on_deactivate=self._on_deactivate,
        )
        self._hotkey.start()

    def reload_hotkey(self, combo: str | None = None) -> None:
        """Réarme le raccourci (après changement de configuration à chaud)."""
        if combo is not None:
            self.config.raccourci = combo
        self._install_hotkey()

    # ------------------------------------------------------------------ #
    # Feedback
    # ------------------------------------------------------------------ #
    def _on_state_change(self, _old: State, new: State) -> None:
        if self._verbose:
            print(f"[état] → {LABELS[new]}")

    def _bip(self, kind: str) -> None:
        if self._beep_enabled:
            _beep(kind)

    # ------------------------------------------------------------------ #
    # Réactions au raccourci
    # ------------------------------------------------------------------ #
    def _on_activate(self) -> None:
        """Combinaison pressée : démarrer (PTT) ou basculer (toggle)."""
        with self._gate:
            mode = (self.config.mode or "push-to-talk").strip().lower()
            if mode == "toggle":
                now = time.monotonic()
                if now - self._last_toggle < _TOGGLE_DEBOUNCE_S:
                    return  # anti-rebond : appui trop rapproché
                self._last_toggle = now
                if self.machine.state is State.REPOS:
                    self._begin_listen()
                elif self.machine.state is State.ECOUTE:
                    self._end_and_process()
            else:  # push-to-talk
                if self.machine.state is State.REPOS:
                    self._begin_listen()

    def _on_deactivate(self) -> None:
        """Combinaison relâchée : en push-to-talk, arrête et transcrit."""
        with self._gate:
            mode = (self.config.mode or "push-to-talk").strip().lower()
            if mode != "toggle" and self.machine.state is State.ECOUTE:
                self._end_and_process()

    # ------------------------------------------------------------------ #
    # Étapes de la dictée
    # ------------------------------------------------------------------ #
    def _begin_listen(self) -> None:
        """Repos → Écoute : démarre la capture micro."""
        try:
            self.machine.to(State.ECOUTE)
        except IllegalTransition:
            return
        try:
            self.recorder.start()
            self._bip("start")
        except Exception as exc:  # noqa: BLE001 - micro indisponible, etc.
            print(f"  [erreur] micro : {exc!s}")
            self._bip("error")
            if self.on_mic_error is not None:
                try:
                    self.on_mic_error(str(exc))
                except Exception:  # noqa: BLE001 - le callback UI ne doit pas planter le moteur
                    pass
            self.machine.to_error()
            self.recorder.abort()
            self.machine.reset()

    def _end_and_process(self) -> None:
        """Écoute → Transcription : arrête la capture et délègue au worker."""
        try:
            audio = self.recorder.stop()
        except Exception as exc:  # noqa: BLE001
            print(f"  [erreur] arrêt capture : {exc!s}")
            self._bip("error")
            self.machine.to_error()
            self.machine.reset()
            return
        self._bip("stop")

        try:
            self.machine.to(State.TRANSCRIPTION)
        except IllegalTransition:
            return

        # Travail coûteux hors du thread d'écoute clavier.
        self._worker = threading.Thread(
            target=self._process, args=(audio,), name="dictee-worker", daemon=True
        )
        self._worker.start()

    def _process(self, audio) -> None:  # noqa: ANN001 - np.ndarray
        """Transcrit puis colle (thread worker). Revient toujours à Repos."""
        try:
            duree = len(audio) / 16_000 if len(audio) else 0.0
            if self._verbose:
                print(f"  audio capté : {duree:.1f}s")

            result = self.transcriber.transcribe(audio)
            if self._verbose:
                print("  " + "-" * 56)
                print(f"  {result.text or '(transcription vide)'}")
                print("  " + "-" * 56)
                print(
                    f"  langue={result.language} "
                    f"(p={result.language_probability:.2f}) · "
                    f"transcription {result.elapsed:.2f}s"
                )

            if not result.text:
                # Silence / bruit : rien à coller, retour direct au repos.
                self.machine.to(State.REPOS)
                return

            self.machine.to(State.INJECTION)
            inject(
                result.text,
                delai_restauration_ms=self.config.delai_restauration_ms,
            )
            self._bip("done")
            if self._verbose:
                print("  ✔ Texte collé, presse-papiers restauré.")
            self.machine.to(State.REPOS)
        except Exception as exc:  # noqa: BLE001 - on revient toujours à Repos
            print(f"  [erreur] {exc!s}")
            self._bip("error")
            self.machine.to_error()
            self.machine.reset()
