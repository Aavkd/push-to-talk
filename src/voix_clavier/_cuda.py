"""Rend les DLL CUDA/cuDNN trouvables sur Windows.

Les paquets pip ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` installent leurs
DLL sous ``site-packages/nvidia/*/bin``, un emplacement que Windows ne fouille
pas par défaut. Sans cela, CTranslate2 échoue à l'exécution avec
``Library cublas64_12.dll is not found or cannot be loaded``.

On enregistre donc ces dossiers via ``os.add_dll_directory`` (et on les ajoute
au ``PATH`` du processus par sécurité, pour le loader natif de CTranslate2).
À appeler **avant** d'importer / charger faster-whisper.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def ensure_cuda_dll_path() -> list[str]:
    """Enregistre les dossiers ``bin`` des paquets nvidia-*-cu12.

    No-op hors Windows. Renvoie la liste des dossiers effectivement ajoutés
    (utile pour le diagnostic).
    """
    if sys.platform != "win32":
        return []

    added: list[str] = []
    for site_dir in _site_packages_dirs():
        nvidia_root = site_dir / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for bin_dir in nvidia_root.glob("*/bin"):
            bin_str = str(bin_dir)
            try:
                os.add_dll_directory(bin_str)
            except (OSError, FileNotFoundError):
                continue
            if bin_str not in os.environ.get("PATH", ""):
                os.environ["PATH"] = bin_str + os.pathsep + os.environ.get("PATH", "")
            added.append(bin_str)
    return added


def _site_packages_dirs() -> list[Path]:
    dirs: list[Path] = []
    for entry in sys.path:
        if entry and entry.endswith("site-packages"):
            p = Path(entry)
            if p.is_dir():
                dirs.append(p)
    # Repli : dossier site-packages du venv courant.
    venv_sp = Path(sys.prefix) / "Lib" / "site-packages"
    if venv_sp.is_dir() and venv_sp not in dirs:
        dirs.append(venv_sp)
    return dirs
