"""Lancement au démarrage de Windows (Phase 7).

Pilote l'inscription de l'application dans la clé de registre ``Run`` de
l'utilisateur courant :

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

C'est l'emplacement recommandé pour un lancement par-utilisateur, sans droits
administrateur. L'état du registre est aligné sur le réglage ``lancer_au_demarrage``
de la configuration (Phase 6) via :func:`sync`, appelée au démarrage de l'app et à
chaque enregistrement des paramètres.

Commande inscrite :

- application **packagée** (Phase 8, ``sys.frozen``) : le chemin de l'exécutable ;
- exécution **depuis les sources** : ``pythonw.exe -m voix_clavier.app`` (``pythonw``
  évite l'ouverture d'une console au démarrage de session).

Tout est *best-effort* : un échec de registre est journalisé mais ne plante jamais
l'application.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .logsetup import get_logger

# Sous-clé Run de l'utilisateur courant (pas de droits admin requis).
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Nom de la valeur ; doit rester stable pour pouvoir la mettre à jour / supprimer.
_VALUE_NAME = "VoixClavier"

_log = get_logger("autostart")


def is_supported() -> bool:
    """Le lancement au démarrage n'est géré que sous Windows."""
    return sys.platform == "win32"


def launch_command() -> str:
    """Construit la ligne de commande à inscrire dans le registre."""
    if getattr(sys, "frozen", False):  # exécutable packagé (Phase 8)
        return f'"{sys.executable}"'
    # Depuis les sources : préférer pythonw (sans console) au python du venv.
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else python
    return f'"{interpreter}" -m voix_clavier.app'


def is_enabled() -> bool:
    """Indique si l'entrée d'autostart existe déjà dans le registre."""
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:  # pragma: no cover - accès registre refusé
        _log.warning("Lecture du registre autostart impossible : %s", exc)
        return False


def enable() -> bool:
    """Inscrit l'application au démarrage. Renvoie ``True`` en cas de succès."""
    if not is_supported():
        _log.warning("Autostart non supporté sur cette plateforme (%s).", sys.platform)
        return False
    import winreg

    command = launch_command()
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command)
        _log.info("Autostart activé : %s", command)
        return True
    except OSError as exc:
        _log.error("Échec d'activation de l'autostart : %s", exc)
        return False


def disable() -> bool:
    """Retire l'application du démarrage. Renvoie ``True`` si l'entrée est absente au final."""
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
        _log.info("Autostart désactivé.")
        return True
    except FileNotFoundError:
        return True  # déjà absent : état conforme
    except OSError as exc:
        _log.error("Échec de désactivation de l'autostart : %s", exc)
        return False


def sync(enabled: bool) -> bool:
    """Aligne le registre sur le réglage ``lancer_au_demarrage`` de la config.

    N'écrit que s'il y a un écart (évite de réécrire la clé à chaque démarrage).
    Renvoie ``True`` si l'état final correspond à ``enabled``.
    """
    if not is_supported():
        return False
    currently = is_enabled()
    if enabled and not currently:
        return enable()
    if not enabled and currently:
        return disable()
    # Déjà conforme. Si activé, on rafraîchit la commande au cas où le chemin de
    # l'interpréteur aurait changé (déplacement du venv, nouveau packaging).
    if enabled:
        return enable()
    return True


def main(argv: list[str] | None = None) -> int:
    """Petit utilitaire de diagnostic : ``python -m voix_clavier.autostart``."""
    import argparse

    from .logsetup import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(
        prog="voix-clavier-autostart",
        description="Gère le lancement au démarrage de Windows (clé de registre Run).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--enable", action="store_true", help="Inscrit l'app au démarrage.")
    group.add_argument("--disable", action="store_true", help="Retire l'app du démarrage.")
    group.add_argument("--status", action="store_true", help="Affiche l'état courant (défaut).")
    args = parser.parse_args(argv)

    if not is_supported():
        print("Le lancement au démarrage n'est géré que sous Windows.")
        return 2

    if args.enable:
        ok = enable()
    elif args.disable:
        ok = disable()
    else:
        ok = True

    state = "activé" if is_enabled() else "désactivé"
    print(f"Lancement au démarrage : {state}")
    print(f"Commande inscrite      : {launch_command()}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
