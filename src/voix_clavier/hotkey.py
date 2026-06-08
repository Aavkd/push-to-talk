"""Raccourci global (Phase 2).

Capte une combinaison de touches (défaut ``Ctrl+Espace``) quelle que soit la
fenêtre au premier plan, via ``pynput``. Contrairement à
``pynput.keyboard.GlobalHotKeys`` (qui n'expose que l'activation), on a besoin de
**distinguer le maintien du relâchement** pour le mode push-to-talk : on suit donc
nous-mêmes l'ensemble des touches pressées.

Deux callbacks :
  - ``on_activate``   : la combinaison complète vient d'être pressée ;
  - ``on_deactivate`` : une des touches de la combinaison vient d'être relâchée
                        alors que la combinaison était active.

L'``on_activate`` n'est émis qu'**une fois** par appui (la répétition clavier de
l'OS ne le redéclenche pas), ce qui fournit déjà un anti-rebond de base. Le mode
toggle ignore ``on_deactivate`` ; le mode push-to-talk s'en sert pour arrêter.

Robustesse Windows : lorsqu'un modificateur (Ctrl) est maintenu, ``KeyCode.char``
des lettres devient un caractère de contrôle (Ctrl+S → ``'\\x13'``). On identifie
donc les touches par leur **code virtuel** (``vk``) plutôt que par leur caractère,
ce qui rend la correspondance stable quels que soient les modificateurs actifs.
"""

from __future__ import annotations

import threading
from typing import Callable

from pynput import keyboard


# --------------------------------------------------------------------------- #
# Normalisation des touches en "jetons" canoniques
# --------------------------------------------------------------------------- #
# Modificateurs et touches nommées spéciales → un jeton unique, indépendant du
# côté gauche/droit (ctrl_l / ctrl_r → "ctrl").
_KEY_TOKEN: dict[keyboard.Key, str] = {
    keyboard.Key.ctrl: "ctrl",
    keyboard.Key.ctrl_l: "ctrl",
    keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt",
    keyboard.Key.alt_l: "alt",
    keyboard.Key.alt_r: "alt",
    keyboard.Key.alt_gr: "alt",
    keyboard.Key.shift: "shift",
    keyboard.Key.shift_r: "shift",
    keyboard.Key.cmd: "cmd",
    keyboard.Key.cmd_r: "cmd",
    keyboard.Key.space: "space",
    keyboard.Key.enter: "enter",
    keyboard.Key.tab: "tab",
    keyboard.Key.esc: "esc",
}

# Alias acceptés dans la chaîne de configuration (français/anglais) → jeton.
_ALIAS: dict[str, str] = {
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl", "contrôle": "ctrl",
    "alt": "alt", "option": "alt",
    "shift": "shift", "maj": "shift",
    "win": "cmd", "cmd": "cmd", "super": "cmd", "meta": "cmd", "windows": "cmd",
    "space": "space", "espace": "space", "spacebar": "space", "barre": "space",
    "enter": "enter", "return": "enter", "entree": "enter", "entrée": "enter",
    "tab": "tab", "tabulation": "tab",
    "esc": "esc", "escape": "esc", "echap": "esc", "échap": "esc",
}


def _token_for_key(key: object) -> str | None:
    """Jeton canonique d'une touche pressée (objet ``pynput``)."""
    if isinstance(key, keyboard.Key):
        token = _KEY_TOKEN.get(key)
        if token is not None:
            return token
        # Touches nommées sans alias dédié (f1..f12, etc.) : utiliser leur nom.
        return key.name
    if isinstance(key, keyboard.KeyCode):
        if key.vk is not None:
            return f"vk{key.vk}"
        if key.char is not None:
            return key.char.lower()
    return None


def _token_for_combo_part(part: str) -> str:
    """Jeton canonique d'un élément de la combinaison configurée (ex. ``"ctrl"``)."""
    p = part.strip().lower()
    if p in _ALIAS:
        return _ALIAS[p]
    if len(p) == 1:
        # Lettres / chiffres : on cible le code virtuel Windows (= ord de la
        # majuscule pour A-Z et des chiffres), stable même modificateurs pressés.
        if p.isalnum():
            return f"vk{ord(p.upper())}"
        return p
    # f-keys et autres noms (« f5 », …) : tels quels.
    return p


def parse_combo(combo: str) -> frozenset[str]:
    """Transforme ``"ctrl+space"`` en ensemble de jetons ``{"ctrl", "space"}``."""
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError(f"Raccourci vide ou invalide : {combo!r}")
    return frozenset(_token_for_combo_part(p) for p in parts)


# --------------------------------------------------------------------------- #
# Raccourci global
# --------------------------------------------------------------------------- #
Callback = Callable[[], None]


class GlobalHotkey:
    """Écoute une combinaison globale et émet activation / désactivation.

    Utilisable comme gestionnaire de contexte ou via :meth:`start` / :meth:`stop`.
    """

    def __init__(
        self,
        combo: str,
        on_activate: Callback,
        on_deactivate: Callback | None = None,
    ) -> None:
        self.combo = combo
        self._required = parse_combo(combo)
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate

        self._pressed: set[str] = set()
        self._active = False
        self._lock = threading.Lock()
        self._listener: keyboard.Listener | None = None

    # -- cycle de vie ------------------------------------------------------- #
    def start(self) -> None:
        """Démarre l'écoute globale (thread dédié géré par pynput)."""
        if self._listener is not None:
            return
        self._pressed.clear()
        self._active = False
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._listener.start()

    def stop(self) -> None:
        """Arrête l'écoute et oublie l'état des touches."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._pressed.clear()
        self._active = False

    def __enter__(self) -> "GlobalHotkey":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # -- callbacks pynput --------------------------------------------------- #
    def _on_press(self, key: object) -> None:
        token = _token_for_key(key)
        if token is None:
            return
        with self._lock:
            self._pressed.add(token)
            # La combinaison complète vient-elle d'être satisfaite ?
            if not self._active and self._required <= self._pressed:
                self._active = True
                fire = self._on_activate
            else:
                fire = None
        if fire is not None:
            fire()

    def _on_release(self, key: object) -> None:
        token = _token_for_key(key)
        if token is None:
            return
        with self._lock:
            self._pressed.discard(token)
            if self._active and not (self._required <= self._pressed):
                self._active = False
                fire = self._on_deactivate
            else:
                fire = None
        if fire is not None:
            fire()
