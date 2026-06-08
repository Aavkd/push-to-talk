"""Injection de texte au curseur via le presse-papiers (Phase 1, fiabilisé Phase 3).

Flux design en 5 étapes (Section 3 des wireframes) qui garantit le rendu correct
des accents français, contrairement à la frappe simulée caractère par caractère :

1. Sauvegarder le presse-papiers courant.
2. Écrire le texte transcrit dedans.
3. Simuler ``Ctrl+V``.
4. Attendre ``delai_restauration_ms`` (~80 ms) pour que le collage soit pris en compte.
5. Restaurer le presse-papiers original.

Durcissements de la Phase 3 :

- **Non-texte préservé** : la sauvegarde/restauration passe par :mod:`voix_clavier.clipboard`
  (API Win32 native) et capture *tous* les formats — image, fichiers, HTML… — et
  pas seulement le texte. Le contenu utilisateur n'est donc jamais corrompu.
- **Accents** : le texte est écrit en ``CF_UNICODETEXT`` (UTF-16), donc é à ç œ
  « » sont collés tels quels.
- **Transcriptions vides** : rien n'est collé (silence / bruit).
- **Enchaînement rapide** : un verrou sérialise les injections pour qu'une dictée
  ne lise/écrase pas le presse-papiers d'une autre en cours de collage.

Le ``Ctrl+V`` est simulé via l'API Win32 native ``SendInput`` (``ctypes``), sans
dépendance supplémentaire.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from . import clipboard

# --------------------------------------------------------------------------- #
# Simulation Ctrl+V via Win32 SendInput
# --------------------------------------------------------------------------- #
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1

# ULONG_PTR : entier de la taille d'un pointeur (8 octets en 64 bits).
ULONG_PTR = ctypes.c_size_t

# Sérialise les injections : deux dictées enchaînées rapidement ne doivent pas
# lire/restaurer le presse-papiers en même temps (corruption d'état).
_inject_lock = threading.Lock()


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
def inject(text: str, *, delai_restauration_ms: int = 80, restore: bool = True) -> bool:
    """Colle ``text`` au curseur via le flux presse-papiers en 5 étapes.

    Args:
        text: texte à coller. Vide → rien n'est collé (silence / bruit).
        delai_restauration_ms: attente entre le collage simulé et la restauration
            du presse-papiers d'origine. Trop court = collage manqué ; trop long
            = latence perçue. Réglable dans ``config.toml``.
        restore: si ``True``, restaure fidèlement le presse-papiers d'origine
            (tous formats, texte comme non-texte) après le collage.

    Returns:
        ``True`` si un collage a été effectué, ``False`` si ``text`` était vide.
    """
    if not text:
        return False

    with _inject_lock:
        # 1. Sauvegarder l'intégralité du presse-papiers (texte ET non-texte :
        #    image, fichiers, HTML…). La capture est best-effort : si elle
        #    échoue, on colle quand même mais sans pouvoir restaurer.
        previous: dict[int, bytes] | None = None
        if restore:
            try:
                previous = clipboard.snapshot()
            except Exception:  # noqa: BLE001 - presse-papiers verrouillé, etc.
                previous = None

        # 2. Écrire le texte transcrit (UTF-16 → accents corrects).
        clipboard.set_text(text)

        # 3. Simuler Ctrl+V.
        _send_ctrl_v()

        # 4. Laisser le temps à l'application cible de prendre en compte le collage.
        time.sleep(max(0, delai_restauration_ms) / 1000.0)

        # 5. Restaurer le presse-papiers original (tous formats), best-effort.
        if restore and previous is not None:
            try:
                clipboard.restore(previous)
            except Exception:  # noqa: BLE001 - ne jamais planter sur la restauration
                pass

    return True
