"""Boucle Phase 1 : capture micro → transcription → injection, sans UI.

C'est la validation du cœur risqué (audio → texte → injection). Le raccourci
global propre (``pynput``, fonctionnant quelle que soit la fenêtre active) est
réservé à la Phase 2 : ici, le déclencheur est **temporaire et codé en dur**,
piloté depuis la console avec la touche Entrée.

Limite assumée de ce déclencheur console : c'est la fenêtre du terminal qui a le
focus, pas l'application cible. Après l'arrêt de l'enregistrement, un court
compte à rebours (``--focus-delay``) laisse le temps de cliquer dans la fenêtre
où le texte doit être collé. La Phase 2 supprime cette gymnastique.

Usage :
    python -m voix_clavier.dictate
    python -m voix_clavier.dictate --device cpu --compute-type int8
    python -m voix_clavier.dictate --list-devices
"""

from __future__ import annotations

import argparse
import sys
import time

from .audio import Recorder, list_input_devices
from .config import load_config
from .injection import inject
from .state import LABELS, State
from .transcription import Transcriber


def _force_utf8_console() -> None:
    """La console Windows est souvent en cp1252 : forcer l'UTF-8 pour les accents."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


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


def _announce(state: State) -> None:
    print(f"[état] → {LABELS[state]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voix-clavier-dictate",
        description="Boucle de dictée Phase 1 (capture → transcription → injection).",
    )
    parser.add_argument("--config", default=None, help="Chemin du config.toml.")
    parser.add_argument("--model", default=None, help="Surcharge le modèle (ex. tiny, large-v3).")
    parser.add_argument("--device", default=None, help="Surcharge le device (cuda | cpu).")
    parser.add_argument("--compute-type", default=None, help="Surcharge le compute_type.")
    parser.add_argument("--language", default=None, help="Surcharge la langue (fr, auto, ...).")
    parser.add_argument(
        "--focus-delay", type=float, default=2.0,
        help="Délai (s) avant collage pour cliquer dans la fenêtre cible (défaut : 2.0).",
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="Liste les périphériques d'entrée audio puis quitte.",
    )
    return parser


def _run_cycle(recorder: Recorder, transcriber: Transcriber, focus_delay: float) -> None:
    """Un cycle complet de dictée. Lève en cas d'erreur (géré par l'appelant)."""
    # --- Écoute ---
    _announce(State.ECOUTE)
    _beep("start")
    recorder.start()
    input("  ● Enregistrement en cours… Entrée pour ARRÊTER.")
    audio = recorder.stop()
    _beep("stop")

    duree = len(audio) / 16_000
    print(f"  audio capté : {duree:.1f}s")

    # --- Transcription ---
    _announce(State.TRANSCRIPTION)
    result = transcriber.transcribe(audio)
    print("  " + "-" * 56)
    print(f"  {result.text or '(transcription vide)'}")
    print("  " + "-" * 56)
    print(
        f"  langue={result.language} (p={result.language_probability:.2f}) · "
        f"transcription {result.elapsed:.2f}s"
    )

    if not result.text:
        # Transcription vide (silence / bruit) : ne rien coller.
        print("  Rien à coller.")
        return

    # --- Injection ---
    _announce(State.INJECTION)
    if focus_delay > 0:
        print(
            f"  Collage dans {focus_delay:.0f}s — cliquez MAINTENANT dans la "
            f"fenêtre cible…"
        )
        time.sleep(focus_delay)
    inject(result.text, delai_restauration_ms=recorder.config.delai_restauration_ms)
    _beep("done")
    print("  ✔ Texte collé, presse-papiers restauré.")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = build_parser().parse_args(argv)

    config = load_config(args.config)
    if args.model:
        config.modele = args.model
    if args.device:
        config.device = args.device
    if args.compute_type:
        config.compute_type = args.compute_type
    if args.language is not None:
        config.langue = args.language

    if args.list_devices:
        print("Périphériques d'entrée audio :")
        for idx, name in list_input_devices():
            print(f"  [{idx}] {name}")
        return 0

    print(
        f"[config] modèle={config.modele} device={config.device} "
        f"compute_type={config.compute_type} langue={config.langue or 'auto'} "
        f"micro={config.peripherique or 'défaut'}"
    )

    transcriber = Transcriber(config)
    transcriber.load()
    recorder = Recorder(config)

    print()
    print("=" * 60)
    print(" Voix → Clavier — boucle Phase 1 (déclencheur console temporaire)")
    print(" Entrée  : démarrer une dictée")
    print(" Ctrl+C  : quitter")
    print("=" * 60)

    state = State.REPOS
    while True:
        _announce(state)
        try:
            input("Entrée pour DÉMARRER une dictée (Ctrl+C pour quitter)…")
        except (EOFError, KeyboardInterrupt):
            print("\nArrêt.")
            recorder.abort()
            return 0

        try:
            _run_cycle(recorder, transcriber, args.focus_delay)
        except KeyboardInterrupt:
            print("\n  Cycle interrompu.")
            recorder.abort()
        except Exception as exc:  # noqa: BLE001 - on revient toujours à Repos
            _beep("error")
            print(f"  [erreur] {exc!s}")
            recorder.abort()
        finally:
            state = State.REPOS
            print()


if __name__ == "__main__":
    raise SystemExit(main())
