"""Surveillance du fichier de configuration pour rechargement à chaud (Phase 2).

Un thread *daemon* observe la date de modification de ``config.toml`` et, à
chaque changement, recharge la config et notifie un callback. Volontairement
simple (sondage du ``mtime``, ~1 s) : pas de dépendance supplémentaire type
``watchdog``, suffisant pour une édition manuelle occasionnelle.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from .config import Config, load_config

OnReload = Callable[[Config], None]


class ConfigWatcher:
    """Recharge ``config.toml`` quand il change et appelle ``on_reload``."""

    def __init__(
        self,
        path: str | Path,
        on_reload: OnReload,
        *,
        interval: float = 1.0,
    ) -> None:
        self.path = Path(path)
        self._on_reload = on_reload
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        try:
            self._last_mtime = self.path.stat().st_mtime
        except OSError:
            self._last_mtime = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="config-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval * 2)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                continue
            if mtime == self._last_mtime:
                continue
            self._last_mtime = mtime
            try:
                new_config = load_config(self.path)
            except Exception as exc:  # noqa: BLE001 - TOML invalide en cours d'édition
                print(f"[config] rechargement ignoré (fichier invalide) : {exc!s}")
                continue
            try:
                self._on_reload(new_config)
            except Exception as exc:  # noqa: BLE001
                print(f"[config] erreur d'application : {exc!s}")
