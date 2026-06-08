"""Emplacements de fichiers, robustes au **packaging** (Phase 8).

En développement (exécution depuis les sources), la configuration vit à la racine
du projet et les modèles sont mis en cache à l'emplacement par défaut de
``huggingface_hub`` (``~/.cache/huggingface``). C'est ce qui a fonctionné des
Phases 0 à 7.

Une fois l'application **packagée** avec PyInstaller (``sys.frozen``), ces deux
hypothèses tombent :

- le dossier de l'exécutable (ou le dossier temporaire ``_MEIPASS`` de
  l'archive onefile) n'est **pas inscriptible** de façon fiable — il faut écrire
  la config et les journaux dans un répertoire utilisateur ;
- aucun cache Hugging Face n'existe forcément sur la machine cible — on dirige le
  téléchargement du modèle (Phase 8) vers un dossier de données applicatif stable.

Ce module centralise donc tous les chemins et masque la différence
dev / packagé au reste du code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Nom du dossier de données applicatif (sous %LOCALAPPDATA% / ~/.local/share).
_APP_DIR_NAME = "VoixClavier"
_CONFIG_FILENAME = "config.toml"


def is_frozen() -> bool:
    """Vrai si l'on tourne depuis un exécutable packagé (PyInstaller)."""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    """Racine du projet en exécution depuis les sources (``src/..``)."""
    return Path(__file__).resolve().parents[2]


def bundle_dir() -> Path:
    """Répertoire des ressources embarquées.

    - packagé : le dossier d'extraction ``_MEIPASS`` (onefile) ou le dossier de
      l'exécutable (onedir) ;
    - dev : la racine du projet.
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return project_root()


def user_data_dir() -> Path:
    """Répertoire de données *inscriptible* par utilisateur.

    Sous Windows : ``%LOCALAPPDATA%\\VoixClavier``. Ailleurs (dev/tests) :
    ``~/.local/share/VoixClavier``. Le dossier n'est pas créé ici ; les appelants
    qui écrivent (config, logs, modèles) le créent à la demande.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / _APP_DIR_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / _APP_DIR_NAME
    return Path.home() / ".local" / "share" / _APP_DIR_NAME


def config_path() -> Path:
    """Chemin du ``config.toml`` effectif.

    - packagé : dans le dossier de données utilisateur (inscriptible et stable
      entre les versions) ;
    - dev : à la racine du projet, comme jusqu'ici.
    """
    if is_frozen():
        return user_data_dir() / _CONFIG_FILENAME
    return project_root() / _CONFIG_FILENAME


def default_config_template() -> Path:
    """``config.toml`` par défaut embarqué dans le paquet (graine au 1er lancement)."""
    return bundle_dir() / _CONFIG_FILENAME


def seed_user_config() -> Path:
    """Crée le ``config.toml`` utilisateur depuis le modèle embarqué si absent.

    No-op en dev (le fichier de la racine fait déjà foi) et best-effort en
    packagé : un échec d'écriture n'empêche pas l'app de démarrer (elle retombe
    alors sur les valeurs par défaut de :class:`~voix_clavier.config.Config`).
    Renvoie le chemin de la configuration utilisateur.
    """
    target = config_path()
    if not is_frozen() or target.exists():
        return target
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        template = default_config_template()
        if template.exists():
            target.write_bytes(template.read_bytes())
    except OSError:
        # On laisse l'app démarrer sur les défauts ; pas de config persistée.
        pass
    return target


def model_cache_dir() -> Path | None:
    """Dossier de cache des modèles Whisper, ou ``None`` pour le cache par défaut.

    - packagé : ``%LOCALAPPDATA%\\VoixClavier\\models`` — emplacement stable, à
      l'écart du cache Hugging Face de l'utilisateur, créé à la demande ;
    - dev : ``None`` pour conserver le cache Hugging Face habituel (et ne pas
      re-télécharger les ~3 Go de ``large-v3`` déjà présents).
    """
    if not is_frozen():
        return None
    cache = user_data_dir() / "models"
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return cache
