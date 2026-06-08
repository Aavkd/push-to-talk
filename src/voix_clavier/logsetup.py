"""Journalisation centralisée pour le diagnostic (Phase 7).

L'application reste très bavarde en console via ``print`` (feedback utilisateur en
français : états, transcriptions, repli…). La journalisation **complète** ce
canal sans le remplacer : elle écrit un fichier de log horodaté et tournant, utile
pour diagnostiquer après coup les chemins d'erreur (capture, transcription,
collage, repli GPU/VRAM, autostart) — y compris quand l'app tourne sans console
(lancée au démarrage de Windows via ``pythonw``).

Pour éviter le double affichage en console, le handler console est volontairement
réglé sur ``WARNING`` : les messages informatifs vont au fichier, seuls les
avertissements/erreurs remontent aussi à l'écran (en plus des ``print`` existants).

Emplacement du fichier : ``%LOCALAPPDATA%\\VoixClavier\\logs`` sous Windows
(répertoire inscriptible même pour une app packagée — Phase 8), avec repli sur un
dossier ``logs/`` à la racine du projet.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import paths

_LOGGER_NAME = "voix_clavier"
_configured = False

_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def log_dir() -> Path:
    """Répertoire des journaux (créé à la demande par :func:`setup_logging`).

    Sous le dossier de données utilisateur inscriptible (``%LOCALAPPDATA%``
    sous Windows), partagé avec la config et le cache modèle — voir
    :mod:`voix_clavier.paths`. Repli dev sur ``logs/`` à la racine du projet.
    """
    if sys.platform == "win32":
        return paths.user_data_dir() / "logs"
    return paths.project_root() / "logs"


def log_file() -> Path:
    """Chemin du fichier de log courant."""
    return log_dir() / "voix-clavier.log"


def setup_logging(level: int = logging.INFO, *, console: bool = True) -> logging.Logger:
    """Configure la journalisation applicative (idempotent).

    - Fichier tournant (1 Mo × 3 sauvegardes) en UTF-8 ;
    - console à partir de ``WARNING`` (pour ne pas doubler les ``print``).

    L'échec d'ouverture du fichier de log ne doit jamais empêcher l'app de
    démarrer : on se rabat alors sur la console seule.
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(_FORMAT, _DATEFMT)

    try:
        directory = log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / "voix-clavier.log",
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)
    except OSError as exc:  # pragma: no cover - disque plein / droits, etc.
        print(f"[log] fichier de log indisponible ({exc!s}) — journalisation console seule.")

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        # WARNING+ seulement : l'info utilisateur passe déjà par les print français.
        console_handler.setLevel(logging.WARNING)
        logger.addHandler(console_handler)

    _configured = True
    logger.info("Journalisation initialisée → %s", log_file())
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Renvoie un logger enfant du logger applicatif (ex. ``"engine"``).

    Sûr à appeler avant :func:`setup_logging` : tant que la configuration n'a pas
    eu lieu, les messages sont simplement ignorés (pas de handler).
    """
    base = logging.getLogger(_LOGGER_NAME)
    return base.getChild(name) if name else base
