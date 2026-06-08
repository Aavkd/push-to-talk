"""CLI de validation — Phase 0.

Critère de validation de la Phase 0 : un script en ligne de commande transcrit
un ``.wav`` français correctement, sur GPU, en moins de ~2 s pour quelques
phrases.

Usage :
    python -m voix_clavier.cli "Enregistrement.wav"
    python -m voix_clavier.cli "Enregistrement.wav" --device cpu --compute-type int8
    python -m voix_clavier.cli "Enregistrement.wav" --config config.toml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .transcription import Transcriber


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voix-clavier-transcribe",
        description="Transcrit un fichier audio français en local (Phase 0).",
    )
    parser.add_argument("audio", type=Path, help="Fichier audio à transcrire (.wav, ...).")
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Chemin du fichier config.toml (défaut : config.toml à la racine).",
    )
    parser.add_argument("--model", default=None, help="Surcharge le modèle (ex. tiny, large-v3).")
    parser.add_argument("--device", default=None, help="Surcharge le device (cuda | cpu).")
    parser.add_argument("--compute-type", default=None, help="Surcharge le compute_type.")
    parser.add_argument("--language", default=None, help="Surcharge la langue (fr, auto, ...).")
    return parser


def main(argv: list[str] | None = None) -> int:
    # La console Windows est souvent en cp1252 : forcer l'UTF-8 pour afficher
    # correctement les accents français (é à ç …) sans dépendre de PYTHONUTF8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    args = build_parser().parse_args(argv)

    if not args.audio.exists():
        print(f"Erreur : fichier introuvable : {args.audio}", file=sys.stderr)
        return 2

    config = load_config(args.config)
    # Surcharges CLI (pratiques pour tester GPU vs CPU sans éditer la config).
    if args.model:
        config.modele = args.model
    if args.device:
        config.device = args.device
    if args.compute_type:
        config.compute_type = args.compute_type
    if args.language is not None:
        config.langue = args.language

    print(
        f"[config] modèle={config.modele} device={config.device} "
        f"compute_type={config.compute_type} langue={config.langue or 'auto'}"
    )

    transcriber = Transcriber(config)
    transcriber.load()

    print(f"[transcription] Fichier : {args.audio}")
    result = transcriber.transcribe(args.audio)

    rtf = result.elapsed / result.duration_audio if result.duration_audio else float("nan")
    print("-" * 60)
    print(result.text)
    print("-" * 60)
    print(
        f"[résultat] langue détectée : {result.language} "
        f"(p={result.language_probability:.2f})"
    )
    print(
        f"[résultat] audio : {result.duration_audio:.1f}s · "
        f"transcription : {result.elapsed:.2f}s · "
        f"RTF : {rtf:.2f}x (modèle réellement utilisé : "
        f"{transcriber.loaded_with})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
