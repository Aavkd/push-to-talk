"""Voix → Clavier — dictée vocale locale pour Windows.

Architecture par modules (voir roadmap, Phase 0) :

- :mod:`voix_clavier.config`        — lecture/écriture de ``config.toml``.
- :mod:`voix_clavier.transcription` — chargement unique du modèle Whisper + STT.
- :mod:`voix_clavier.audio`         — capture micro (Phase 1).
- :mod:`voix_clavier.injection`     — injection presse-papiers (Phases 1 et 3).
- :mod:`voix_clavier.clipboard`     — presse-papiers Win32 natif, tous formats (Phase 3).
- :mod:`voix_clavier.hotkey`        — raccourci global ``pynput`` (Phase 2).
- :mod:`voix_clavier.state`         — machine à états explicite (Phase 2).
- :mod:`voix_clavier.engine`        — orchestration raccourci → dictée (Phase 2).
- :mod:`voix_clavier.watcher`       — rechargement à chaud de la config (Phase 2).
- :mod:`voix_clavier.app`           — application pilotée par le raccourci (Phase 2).
- :mod:`voix_clavier.selftest`      — banc de validation presse-papiers (Phase 3).
- :mod:`voix_clavier.ui`            — systray / pilule / paramètres (Phases 4-6).
- :mod:`voix_clavier.autostart`     — lancement au démarrage de Windows (Phase 7).
- :mod:`voix_clavier.paths`         — chemins inscriptibles, dev vs packagé (Phase 8).
- :mod:`voix_clavier.models`        — téléchargement du modèle + progression (Phase 8).
- :mod:`voix_clavier.doctor`        — diagnostic de l'environnement (Phase 8).

Phases 0-8 implémentées : transcription, config, capture, injection robuste,
raccourci global, machine à états et modes, UI (systray, pilule, paramètres),
démarrage Windows et replis GPU/CPU/VRAM, puis packaging PyInstaller avec
téléchargement du modèle au premier lancement et retour visuel.
"""

__version__ = "0.1.0"
