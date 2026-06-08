"""Icône de barre des tâches (systray) — Phase 4.

Première couche d'interface visible : une icône ``pystray`` qui

1. **reflète l'état courant** de la machine à états (Phase 2) via quatre visuels
   (repos / écoute / traitement / erreur, cf. :mod:`voix_clavier.ui.icons`) ;
2. offre un **menu clic-droit** (variante « Settings C » des wireframes) : ligne
   d'état, accès aux paramètres (Phase 6), bascule rapide du mode, choix de la
   langue, et « Quitter » ;
3. émet des **notifications toast** (~4 s) : prêt au démarrage, repli GPU→CPU,
   erreur micro (Section 5 des wireframes).

Le systray est délibérément découplé du moteur : il reçoit l'objet
:class:`~voix_clavier.engine.DictationEngine` et s'abonne à sa machine à états.
Les mises à jour d'icône arrivent depuis les threads du moteur (écoute clavier,
worker de transcription) ; ``pystray`` accepte la mise à jour de ``icon.icon`` et
``icon.update_menu()`` depuis un autre thread sous Windows.
"""

from __future__ import annotations

from typing import Callable

import pystray

from ..config import save_config
from ..engine import DictationEngine
from ..state import LABELS, State
from . import icons

# Langues proposées dans le menu (libellé affiché -> valeur de config).
_LANGUAGES: list[tuple[str, str]] = [
    ("Français", "fr"),
    ("Anglais", "en"),
    ("Auto-détection", "auto"),
]

# Modes proposés dans le sous-menu « Mode ».
_MODES: list[tuple[str, str]] = [
    ("Push-to-talk", "push-to-talk"),
    ("Toggle", "toggle"),
]

# Libellés courts d'état pour la ligne d'information du menu.
_STATUS_TEXT: dict[State, str] = {
    State.REPOS: "prêt",
    State.ECOUTE: "écoute…",
    State.TRANSCRIPTION: "transcription…",
    State.INJECTION: "collage…",
    State.ERREUR: "erreur",
}


def _normalise_mode(value: str) -> str:
    return (value or "push-to-talk").strip().lower()


def _normalise_lang(value: str) -> str:
    v = (value or "").strip().lower()
    return "auto" if v in ("", "auto") else v


class Systray:
    """Icône systray pilotée par l'état du moteur de dictée."""

    def __init__(
        self,
        engine: DictationEngine,
        *,
        on_open_settings: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        persist: bool = True,
    ) -> None:
        self.engine = engine
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        # Écrit les changements (mode / langue) dans config.toml pour qu'ils
        # survivent au redémarrage. Le watcher relit alors le fichier sans heurt.
        self._persist = persist

        self._icon = pystray.Icon(
            "voix-clavier",
            icon=icons.icon_for(engine.machine.state),
            title=self._title(engine.machine.state),
            menu=self._build_menu(),
        )
        # S'abonner à la machine à états pour suivre les transitions en direct.
        engine.machine.subscribe(self._on_state_change)

    def set_on_quit(self, callback: Callable[[], None]) -> None:
        """Définit l'action « Quitter » (p. ex. arrêter la boucle Qt en Phase 5)."""
        self._on_quit = callback

    def set_on_open_settings(self, callback: Callable[[], None]) -> None:
        """Définit l'action « Paramètres… » (ouvre la fenêtre de la Phase 6)."""
        self._on_open_settings = callback

    # ------------------------------------------------------------------ #
    # Construction du menu (variante Settings C)
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> pystray.Menu:
        Item = pystray.MenuItem
        return pystray.Menu(
            Item(lambda _i: f"État : {_STATUS_TEXT[self.engine.machine.state]}",
                 None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item("Paramètres…", self._open_settings),
            Item("Mode", pystray.Menu(*self._mode_items())),
            Item("Langue", pystray.Menu(*self._language_items())),
            pystray.Menu.SEPARATOR,
            Item("Quitter", self._quit),
        )

    def _mode_items(self) -> list[pystray.MenuItem]:
        items: list[pystray.MenuItem] = []
        for label, value in _MODES:
            items.append(
                pystray.MenuItem(
                    label,
                    self._make_set_mode(value),
                    checked=self._make_mode_checked(value),
                    radio=True,
                )
            )
        return items

    def _language_items(self) -> list[pystray.MenuItem]:
        items: list[pystray.MenuItem] = []
        for label, value in _LANGUAGES:
            items.append(
                pystray.MenuItem(
                    label,
                    self._make_set_language(value),
                    checked=self._make_lang_checked(value),
                    radio=True,
                )
            )
        return items

    # --- fabriques de callbacks (capture de la valeur par défaut) --------- #
    def _make_set_mode(self, value: str) -> Callable[[object, object], None]:
        def handler(_icon: object, _item: object) -> None:
            self._set_mode(value)
        return handler

    def _make_mode_checked(self, value: str) -> Callable[[object], bool]:
        return lambda _item: _normalise_mode(self.engine.config.mode) == value

    def _make_set_language(self, value: str) -> Callable[[object, object], None]:
        def handler(_icon: object, _item: object) -> None:
            self._set_language(value)
        return handler

    def _make_lang_checked(self, value: str) -> Callable[[object], bool]:
        return lambda _item: _normalise_lang(self.engine.config.langue) == value

    # ------------------------------------------------------------------ #
    # Actions du menu
    # ------------------------------------------------------------------ #
    def _set_mode(self, value: str) -> None:
        if _normalise_mode(self.engine.config.mode) == value:
            return
        self.engine.config.mode = value
        self._save()
        self._icon.update_menu()

    def _set_language(self, value: str) -> None:
        # "auto" est stocké tel quel ; le moteur le traduit via langue_whisper.
        new = "auto" if value == "auto" else value
        if _normalise_lang(self.engine.config.langue) == _normalise_lang(new):
            return
        self.engine.config.langue = "" if new == "auto" else new
        self._save()
        self._icon.update_menu()

    def _open_settings(self, _icon: object, _item: object) -> None:
        if self._on_open_settings is not None:
            self._on_open_settings()
        else:
            # La fenêtre de paramètres est l'objet de la Phase 6.
            self.notify("Paramètres", "La fenêtre de paramètres arrive en Phase 6.")

    def _quit(self, _icon: object, _item: object) -> None:
        if self._on_quit is not None:
            self._on_quit()
        self.stop()

    def _save(self) -> None:
        if not self._persist:
            return
        try:
            save_config(self.engine.config)
        except Exception as exc:  # noqa: BLE001 - persistance best-effort
            print(f"[systray] échec d'écriture de la config : {exc!s}")

    # ------------------------------------------------------------------ #
    # Suivi de l'état
    # ------------------------------------------------------------------ #
    def _title(self, state: State) -> str:
        return f"Voix → Clavier — {LABELS[state]}"

    def _on_state_change(self, _old: State, new: State) -> None:
        # Appelé depuis un thread du moteur : best-effort, ne jamais lever.
        try:
            self._icon.icon = icons.icon_for(new)
            self._icon.title = self._title(new)
            self._icon.update_menu()
        except Exception:  # noqa: BLE001 - icône pas encore lancée, etc.
            pass

    # ------------------------------------------------------------------ #
    # Notifications toast (Section 5)
    # ------------------------------------------------------------------ #
    def notify(self, title: str, message: str) -> None:
        """Affiche une notification toast (~4 s, best-effort)."""
        try:
            self._icon.notify(message, title)
        except Exception as exc:  # noqa: BLE001 - backend sans support de notify
            print(f"[systray] notification indisponible : {exc!s} ({title} — {message})")

    def notify_ready(self) -> None:
        """Toast de démarrage réussi (ou de repli GPU/VRAM/CPU, Phase 7)."""
        loaded = self.engine.transcriber.loaded_with
        if loaded is None:
            self.notify("Voix → Clavier prêt", "Modèle chargé.")
            return
        model, device, _compute = loaded
        fallback = self.engine.transcriber.fallback
        if fallback == "cpu":
            # Le chargement GPU a entièrement échoué : bascule sur CPU léger.
            self.notify("GPU introuvable", f"Repli sur CPU · modèle {model}")
        elif fallback == "vram":
            # Mémoire GPU insuffisante : repli int8_float16 (toujours sur GPU).
            self.notify(
                "Mémoire GPU insuffisante",
                f"Repli int8_float16 · modèle {model}",
            )
        else:
            cible = "GPU" if device == "cuda" else "CPU"
            self.notify(
                "Voix → Clavier prêt",
                f"Modèle {model} chargé sur {cible}",
            )

    def notify_mic_error(self, detail: str = "") -> None:
        """Toast d'erreur micro (aucun périphérique / capture impossible)."""
        message = "Aucun périphérique détecté"
        if detail:
            message = f"{message} · {detail}"
        self.notify("Erreur micro", message)

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        """Lance la boucle de l'icône (bloquant ; à appeler sur le thread principal)."""
        self._icon.run()

    def run_detached(self) -> None:
        """Lance l'icône dans son propre thread (non bloquant)."""
        self._icon.run_detached()

    def stop(self) -> None:
        try:
            self._icon.stop()
        except Exception:  # noqa: BLE001 - déjà arrêtée
            pass
