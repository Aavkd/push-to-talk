"""Lecture (et écriture) de la configuration ``config.toml``.

Le format TOML est une décision arrêtée de la roadmap : lisible à la main,
commentable, et round-trip propre avec la future fenêtre de paramètres.

La lecture utilise ``tomllib`` (stdlib, Python >= 3.11). L'écriture utilise
``tomli-w`` ; elle servira à la fenêtre de paramètres (Phase 6) et n'est pas
strictement nécessaire en Phase 0.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - repli pour Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


# Emplacement par défaut du fichier de config : à la racine du projet, à côté
# de ce package source. Ajustable lors du packaging (Phase 8).
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.toml"


@dataclass
class Config:
    """Configuration applicative typée, avec les valeurs par défaut arrêtées."""

    # --- [declenchement] ---
    mode: str = "push-to-talk"          # "push-to-talk" | "toggle"
    raccourci: str = "ctrl+space"

    # --- [transcription] ---
    modele: str = "large-v3"
    device: str = "cuda"                # "cuda" | "cpu"
    compute_type: str = "float16"       # "float16" | "int8_float16" | "int8"
    langue: str = "fr"                  # "fr", ... ou "" / "auto" pour auto-détection

    # --- [micro] ---
    peripherique: str = ""              # "" = périphérique par défaut Windows

    # --- [presse_papiers] ---
    delai_restauration_ms: int = 80

    # --- [interface] ---
    variante_pilule: str = "D"
    position_pilule: str = "bas-droite"

    # --- [demarrage] ---
    lancer_au_demarrage: bool = False
    afficher_systray: bool = True

    @property
    def langue_whisper(self) -> str | None:
        """Langue au format attendu par faster-whisper.

        ``None`` déclenche l'auto-détection ; on l'active pour "" ou "auto".
        """
        if self.langue.strip().lower() in ("", "auto"):
            return None
        return self.langue.strip()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Mapping section TOML -> champs du dataclass. Évite de dépendre de l'ordre et
# tolère les sections/clés absentes (on retombe sur les valeurs par défaut).
_SECTION_MAP: dict[str, tuple[str, ...]] = {
    "declenchement": ("mode", "raccourci"),
    "transcription": ("modele", "device", "compute_type", "langue"),
    "micro": ("peripherique",),
    "presse_papiers": ("delai_restauration_ms",),
    "interface": ("variante_pilule", "position_pilule"),
    "demarrage": ("lancer_au_demarrage", "afficher_systray"),
}


def load_config(path: str | Path | None = None) -> Config:
    """Charge la configuration depuis ``path`` (défaut : ``config.toml``).

    Si le fichier est absent, renvoie une :class:`Config` aux valeurs par
    défaut sans lever d'erreur (l'app reste lançable en Phase 0).
    """
    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    config = Config()

    if not cfg_path.exists():
        return config

    with cfg_path.open("rb") as fh:
        data = tomllib.load(fh)

    for section, keys in _SECTION_MAP.items():
        table = data.get(section, {})
        if not isinstance(table, dict):
            continue
        for key in keys:
            if key in table:
                setattr(config, key, table[key])

    return config


def save_config(config: Config, path: str | Path | None = None) -> None:
    """Écrit la configuration au format TOML (utilisé par la fenêtre de paramètres).

    Note : cette écriture ne préserve pas les commentaires du fichier. La
    Phase 6 décidera de la stratégie de round-trip exacte ; pour l'instant on
    fournit une sérialisation correcte et structurée par sections.
    """
    import tomli_w

    cfg_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    structured: dict[str, dict[str, Any]] = {}
    for section, keys in _SECTION_MAP.items():
        structured[section] = {key: getattr(config, key) for key in keys}

    with cfg_path.open("wb") as fh:
        tomli_w.dump(structured, fh)
