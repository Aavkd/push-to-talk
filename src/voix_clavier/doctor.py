"""Diagnostic de l'environnement (Phase 8) : ``python -m voix_clavier.doctor``.

Outil de vérification rapide avant un déploiement ou pour un rapport de bug. Il
résume, sans rien modifier :

- l'interpréteur / le mode (sources vs packagé) et les **droits administrateur** ;
- les chemins effectifs (config, journaux, cache modèle, données utilisateur) ;
- la disponibilité **GPU/CUDA** (DLL trouvées, device CTranslate2) ;
- la **présence du modèle** en cache (un téléchargement est-il nécessaire ?) ;
- les **périphériques micro** détectés.

Sur les **droits administrateur** : le raccourci global (``pynput``) et l'injection
``SendInput`` fonctionnent sans élévation pour les applications standard. Une cible
elle-même lancée en administrateur (UAC) n'accepte toutefois pas les entrées d'un
processus non élevé : si la dictée ne « tape » pas dans une fenêtre précise, lancer
Voix→Clavier en administrateur résout ce cas. C'est documenté dans ``DOCS.md``.
"""

from __future__ import annotations

import sys

from . import paths
from .config import load_config


def is_admin() -> bool:
    """Vrai si le processus courant dispose des droits administrateur (Windows)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - API absente / refusée
        return False


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def _line(label: str, value: object) -> None:
    print(f"  {label:<24} {value}")


def _check_cuda() -> None:
    _section("GPU / CUDA")
    try:
        from ._cuda import ensure_cuda_dll_path

        added = ensure_cuda_dll_path()
        _line("Dossiers DLL CUDA", f"{len(added)} enregistré(s)")
        for d in added:
            _line("", d)
    except Exception as exc:  # noqa: BLE001
        _line("DLL CUDA", f"erreur : {exc}")

    try:
        import ctranslate2

        count = ctranslate2.get_cuda_device_count()
        _line("CTranslate2", getattr(ctranslate2, "__version__", "?"))
        _line("GPU CUDA détectés", count)
    except Exception as exc:  # noqa: BLE001
        _line("CTranslate2", f"indisponible : {exc}")


def _check_model(model: str) -> None:
    _section("Modèle de transcription")
    cache_dir = paths.model_cache_dir()
    _line("Modèle configuré", model)
    _line("Cache modèle", cache_dir or "cache Hugging Face par défaut")
    try:
        from . import models

        cached = models.is_model_cached(model, cache_dir)
        _line("Déjà téléchargé", "oui" if cached else "non (téléchargement au 1er lancement)")
        _line("Dépôt Hub", models.repo_id(model))
    except Exception as exc:  # noqa: BLE001
        _line("Vérification cache", f"indisponible : {exc}")


def _check_audio() -> None:
    _section("Micro")
    try:
        from .audio import list_input_devices

        devices = list_input_devices()
        _line("Périphériques d'entrée", len(devices))
        for idx, name in devices:
            _line("", f"[{idx}] {name}")
    except Exception as exc:  # noqa: BLE001
        _line("Périphériques d'entrée", f"indisponible : {exc}")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()

    _section("Voix → Clavier — diagnostic (Phase 8)")
    _line("Python", sys.version.split()[0])
    _line("Plateforme", sys.platform)
    _line("Mode", "packagé (frozen)" if paths.is_frozen() else "sources")
    _line("Exécutable", sys.executable)
    _line("Droits administrateur", "oui" if is_admin() else "non")

    _section("Chemins")
    _line("Données utilisateur", paths.user_data_dir())
    _line("config.toml", paths.config_path())
    from .logsetup import log_file

    _line("Journal", log_file())

    config = load_config()
    _section("Configuration active")
    _line("Mode déclenchement", config.mode)
    _line("Raccourci", config.raccourci)
    _line("Device / compute", f"{config.device} / {config.compute_type}")
    _line("Langue", config.langue or "auto")
    _line("Pilule", f"{config.variante_pilule} ({config.position_pilule})")
    _line("Lancer au démarrage", config.lancer_au_demarrage)

    _check_cuda()
    _check_model(config.modele)
    _check_audio()

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
