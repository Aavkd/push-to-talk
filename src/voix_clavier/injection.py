"""Injection de texte au curseur via le presse-papiers (Phase 1, fiabilisé Phase 3).

Flux design en 5 étapes (Section 3 des wireframes) qui garantit le rendu correct
des accents français, contrairement à la frappe simulée caractère par caractère :

1. Sauvegarder le presse-papiers courant.
2. Écrire le texte transcrit dedans (``pyperclip``).
3. Simuler ``Ctrl+V``.
4. Attendre ``delai_restauration_ms`` (~80 ms) pour que le collage soit pris en compte.
5. Restaurer le presse-papiers original.

Le ``Ctrl+V`` est simulé via l'API Win32 native ``SendInput`` (``ctypes``), sans
dépendance supplémentaire. La cible étant exclusivement Windows, c'est fiable et
cela évite de tirer ``pynput`` dès la Phase 1 (réservé au raccourci global, Phase 2).
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import pyperclip

# --------------------------------------------------------------------------- #
# Simulation Ctrl+V via Win32 SendInput
# --------------------------------------------------------------------------- #
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

# ULONG_PTR : entier de la taille d'un pointeur (8 octets en 64 bits).
ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    # L'union doit contenir le plus grand membre (MOUSEINPUT) pour que
    # ``sizeof(_INPUT)`` corresponde à la structure attendue par SendInput.
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.WinDLL("user32", use_last_error=True)


def _key_event(vk: int, flags: int = 0) -> _INPUT:
    return _INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)),
    )


def _send_ctrl_v() -> None:
    """Envoie la combinaison Ctrl(down) V(down) V(up) Ctrl(up)."""
    events = (_INPUT * 4)(
        _key_event(VK_CONTROL),
        _key_event(VK_V),
        _key_event(VK_V, KEYEVENTF_KEYUP),
        _key_event(VK_CONTROL, KEYEVENTF_KEYUP),
    )
    n = _user32.SendInput(len(events), ctypes.byref(events), ctypes.sizeof(_INPUT))
    if n != len(events):
        err = ctypes.get_last_error()
        raise OSError(f"SendInput a échoué (envoyé {n}/{len(events)}, erreur Win32 {err}).")


# --------------------------------------------------------------------------- #
# Injection
# --------------------------------------------------------------------------- #
def inject(text: str, *, delai_restauration_ms: int = 80) -> None:
    """Colle ``text`` au curseur via le flux presse-papiers en 5 étapes.

    Ne fait rien si ``text`` est vide (silence, bruit → rien à coller).
    """
    if not text:
        return

    # 1. Sauvegarder le presse-papiers courant. La gestion du contenu non-texte
    #    (images, fichiers) est l'affaire de la Phase 3 ; ici on capte le texte
    #    et on tolère un échec de lecture sans planter.
    try:
        previous = pyperclip.paste()
    except Exception:  # noqa: BLE001
        previous = None

    # 2. Écrire le texte transcrit.
    pyperclip.copy(text)

    # 3. Simuler Ctrl+V.
    _send_ctrl_v()

    # 4. Laisser le temps à l'application cible de prendre en compte le collage.
    time.sleep(max(0, delai_restauration_ms) / 1000.0)

    # 5. Restaurer le presse-papiers original.
    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:  # noqa: BLE001
            pass
