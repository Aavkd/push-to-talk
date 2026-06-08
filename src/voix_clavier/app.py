"""Application Phase 2 : dictée pilotée par le raccourci global.

Remplace le déclencheur console temporaire de la Phase 1
(:mod:`voix_clavier.dictate`). Ici, la dictée se déclenche **partout dans le
système** via le raccourci configuré (défaut ``Ctrl+Espace``), dans l'un des
deux modes (``push-to-talk`` / ``toggle``) lus depuis ``config.toml``.

Le fichier de configuration est rechargé **à chaud** : modifier le mode, le
raccourci, le micro, la langue ou le délai de presse-papiers prend effet sans
redémarrage. Le changement de modèle / device / compute_type nécessite en
revanche un redémarrage (chargement du modèle coûteux) — un message le signale.

Usage :
    python -m voix_clavier.app
    python -m voix_clavier.app --mode toggle
    python -m voix_clavier.app --list-devices
"""

from __future__ import annotations

import argparse
import sys
import time

from .audio import list_input_devices
from .config import DEFAULT_CONFIG_PATH, Config, load_config
from .engine import DictationEngine
from .watcher import ConfigWatcher

# Champs applicables à chaud (mutation en place de l'objet Config partagé).
_HOT_FIELDS = ("mode", "raccourci", "peripherique", "delai_restauration_ms", "langue")
# Champs exigeant un redémarrage (rechargement du modèle Whisper).
_RESTART_FIELDS = ("modele", "device", "compute_type")


def _force_utf8_console() -> None:
    """La console Windows est souvent en cp1252 : forcer l'UTF-8 pour les accents."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voix-clavier",
        description="Dictée vocale globale (Phase 2 : raccourci + modes + config).",
    )
    parser.add_argument("--config", default=None, help="Chemin du config.toml.")
    parser.add_argument("--mode", default=None, help="Surcharge le mode (push-to-talk | toggle).")
    parser.add_argument("--hotkey", default=None, help="Surcharge le raccourci (ex. ctrl+space).")
    parser.add_argument("--model", default=None, help="Surcharge le modèle (ex. tiny, large-v3).")
    parser.add_argument("--device", default=None, help="Surcharge le device (cuda | cpu).")
    parser.add_argument("--compute-type", default=None, help="Surcharge le compute_type.")
    parser.add_argument("--language", default=None, help="Surcharge la langue (fr, auto, ...).")
    parser.add_argument("--no-beep", action="store_true", help="Désactive les bips de feedback.")
    parser.add_argument("--no-watch", action="store_true", help="Désactive le rechargement à chaud de la config.")
    parser.add_argument(
        "--no-systray", action="store_true",
        help="Force le mode console même si l'icône systray est activée dans la config.",
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="Liste les périphériques d'entrée audio puis quitte.",
    )
    return parser


def _apply_overrides(config: Config, args: argparse.Namespace) -> None:
    if args.mode:
        config.mode = args.mode
    if args.hotkey:
        config.raccourci = args.hotkey
    if args.model:
        config.modele = args.model
    if args.device:
        config.device = args.device
    if args.compute_type:
        config.compute_type = args.compute_type
    if args.language is not None:
        config.langue = args.language


def _make_reload_handler(engine: DictationEngine):
    """Fabrique le callback d'application à chaud d'une nouvelle config."""

    def on_reload(new: Config) -> None:
        current = engine.config
        changed = [
            f
            for f in (*_HOT_FIELDS, *_RESTART_FIELDS)
            if getattr(new, f) != getattr(current, f)
        ]
        if not changed:
            return

        # Champs à chaud : mutation en place de l'objet partagé (le Recorder et
        # le Transcriber lisent ces valeurs au moment voulu).
        for field in _HOT_FIELDS:
            if field in changed:
                setattr(current, field, getattr(new, field))

        if "raccourci" in changed:
            engine.reload_hotkey(current.raccourci)

        applied = [f for f in _HOT_FIELDS if f in changed]
        if applied:
            print(f"[config] rechargée à chaud : {', '.join(applied)}.")

        # Champs lourds : on enregistre la valeur (cohérence d'affichage) mais on
        # avertit qu'un redémarrage est requis pour recharger le modèle.
        restart = [f for f in _RESTART_FIELDS if f in changed]
        if restart:
            for field in restart:
                setattr(current, field, getattr(new, field))
            print(
                f"[config] {', '.join(restart)} modifié(s) : "
                f"redémarrage requis pour recharger le modèle."
            )

    return on_reload


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = build_parser().parse_args(argv)

    if args.list_devices:
        print("Périphériques d'entrée audio :")
        for idx, name in list_input_devices():
            print(f"  [{idx}] {name}")
        return 0

    config_path = args.config or DEFAULT_CONFIG_PATH
    config = load_config(args.config)
    _apply_overrides(config, args)

    print(
        f"[config] mode={config.mode} raccourci={config.raccourci} "
        f"modèle={config.modele} device={config.device} "
        f"compute_type={config.compute_type} langue={config.langue or 'auto'} "
        f"micro={config.peripherique or 'défaut'}"
    )

    engine = DictationEngine(config, beep=not args.no_beep)
    print("[modèle] chargement en cours…")
    engine.start()

    watcher: ConfigWatcher | None = None
    if not args.no_watch:
        watcher = ConfigWatcher(config_path, _make_reload_handler(engine))
        watcher.start()

    mode_aide = (
        "maintenir pour parler, relâcher pour transcrire"
        if config.mode.strip().lower() != "toggle"
        else "un appui démarre, un second arrête et transcrit"
    )
    print()
    print("=" * 64)
    print(" Voix → Clavier — Phase 4 (systray + icônes d'état + toasts)")
    print(f" Raccourci : {config.raccourci}  ({mode_aide})")
    print(" Le texte se colle dans la fenêtre active, où que soit le curseur.")
    print("=" * 64)

    # Systray (Phase 4) : actif si demandé en config et non désactivé en ligne
    # de commande. Il porte l'icône d'état, le menu et les toasts. En son
    # absence (dépendances manquantes ou --no-systray), on retombe sur la
    # boucle console des phases précédentes.
    systray = None
    if config.afficher_systray and not args.no_systray:
        systray = _try_build_systray(engine)

    try:
        if systray is not None:
            engine.on_mic_error = systray.notify_mic_error
            systray.notify_ready()
            print(" Icône systray active — clic droit pour le menu, « Quitter » pour fermer.")
            systray.run()  # bloquant jusqu'à « Quitter »
        else:
            print(" Mode console — Ctrl+C dans cette console pour quitter.")
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        if systray is not None:
            systray.stop()
        if watcher is not None:
            watcher.stop()
        engine.stop()
    return 0


def _try_build_systray(engine: DictationEngine):
    """Construit le systray si les dépendances UI sont présentes, sinon ``None``.

    L'import est différé (et toléré en échec) pour que l'application reste
    lançable en mode console quand ``pystray`` / ``Pillow`` ne sont pas installés.
    """
    try:
        from .ui.systray import Systray
    except ImportError as exc:
        print(
            f"[systray] dépendances UI absentes ({exc!s}) — mode console. "
            f"Installez-les avec : pip install pystray Pillow"
        )
        return None
    try:
        return Systray(engine)
    except Exception as exc:  # noqa: BLE001 - le systray ne doit pas empêcher de dicter
        print(f"[systray] initialisation impossible ({exc!s}) — mode console.")
        return None


if __name__ == "__main__":
    raise SystemExit(main())
