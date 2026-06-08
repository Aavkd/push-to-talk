"""Voix → Clavier — dictée vocale locale pour Windows.

Architecture par modules (voir roadmap, Phase 0) :

- :mod:`voix_clavier.config`        — lecture/écriture de ``config.toml``.
- :mod:`voix_clavier.transcription` — chargement unique du modèle Whisper + STT.
- :mod:`voix_clavier.audio`         — capture micro (Phase 1).
- :mod:`voix_clavier.injection`     — injection presse-papiers (Phase 1).
- :mod:`voix_clavier.hotkey`        — raccourci global ``pynput`` (Phase 2).
- :mod:`voix_clavier.state`         — machine à états explicite (Phase 2).
- :mod:`voix_clavier.engine`        — orchestration raccourci → dictée (Phase 2).
- :mod:`voix_clavier.watcher`       — rechargement à chaud de la config (Phase 2).
- :mod:`voix_clavier.app`           — application pilotée par le raccourci (Phase 2).
- :mod:`voix_clavier.ui`            — systray / pilule / paramètres (Phases 4-6).

Phases 0-2 implémentées : transcription, config, capture, injection, raccourci
global, machine à états et les deux modes de déclenchement. L'UI (systray,
pilule, paramètres) reste à venir (Phases 4-6).
"""

__version__ = "0.1.0"
