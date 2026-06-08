"""Accès natif au presse-papiers Windows (Phase 3).

La Phase 1 se contentait de ``pyperclip``, qui ne manipule que du **texte**.
Problème : pour restaurer fidèlement le presse-papiers après une dictée, il faut
préserver *tout* son contenu, y compris le **non-texte** — une image copiée
(``CF_DIB``), des fichiers (``CF_HDROP``), du HTML, etc. Restaurer uniquement le
texte effacerait silencieusement ce que l'utilisateur avait copié.

Ce module accède donc directement à l'API Win32 du presse-papiers via ``ctypes``
(aucune dépendance supplémentaire) et expose :

- :func:`snapshot` / :func:`restore` : capture et restauration de **tous** les
  formats stockés en mémoire globale (texte, image bitmap, fichiers, formats
  enregistrés comme « HTML Format » ou « PNG »…) ;
- :func:`set_text` / :func:`get_text` : lecture/écriture du texte Unicode ;
- :func:`set_raw` / :func:`register_format` : utilitaires bas niveau (tests).

Les formats adossés à un *handle* GDI plutôt qu'à de la mémoire globale
(``CF_BITMAP``, ``CF_METAFILEPICT``, ``CF_ENHMETAFILE``, ``CF_PALETTE`` et les
formats d'affichage privés) ne peuvent pas être copiés octet à octet ; ils sont
ignorés à la capture. Les images restent néanmoins préservées car Windows fournit
en parallèle leur version mémoire ``CF_DIB`` / ``CF_DIBV5``, que l'on capture.

Cible exclusivement Windows.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

# --------------------------------------------------------------------------- #
# Constantes Win32
# --------------------------------------------------------------------------- #
CF_TEXT = 1
CF_UNICODETEXT = 13
CF_HDROP = 15

# Allocation mémoire globale : déplaçable + initialisée à zéro.
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040
GHND = GMEM_MOVEABLE | GMEM_ZEROINIT

# Formats adossés à un handle non-mémoire (GDI / affichage privé) : on ne sait
# pas les copier octet à octet, donc on les saute à la capture. Les images sont
# tout de même préservées via CF_DIB (8) / CF_DIBV5 (17), eux en mémoire.
_SKIP_FORMATS = frozenset({
    2,      # CF_BITMAP        (HBITMAP)
    3,      # CF_METAFILEPICT  (handle METAFILEPICT)
    9,      # CF_PALETTE       (HPALETTE)
    14,     # CF_ENHMETAFILE   (HENHMETAFILE)
    0x80,   # CF_OWNERDISPLAY
    0x81,   # CF_DSPTEXT
    0x82,   # CF_DSPBITMAP
    0x83,   # CF_DSPMETAFILEPICT
    0x8E,   # CF_DSPENHMETAFILE
})

ULONG_PTR = ctypes.c_size_t

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Signatures explicites : indispensables en 64 bits pour que les *handles* et
# les pointeurs ne soient pas tronqués à 32 bits par le défaut ctypes (c_int).
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClipboardFormatNameW.restype = ctypes.c_int

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalSize.restype = ctypes.c_size_t


class ClipboardError(RuntimeError):
    """Échec d'une opération sur le presse-papiers Windows."""


# --------------------------------------------------------------------------- #
# Ouverture / fermeture (avec réessais)
# --------------------------------------------------------------------------- #
class _ClipboardSession:
    """Gestionnaire de contexte ouvrant le presse-papiers, avec réessais.

    Une autre application peut détenir momentanément le presse-papiers ;
    ``OpenClipboard`` échoue alors. On réessaie brièvement avant d'abandonner.
    """

    def __init__(self, *, attempts: int = 12, delay: float = 0.02) -> None:
        self._attempts = attempts
        self._delay = delay
        self._open = False

    def __enter__(self) -> "_ClipboardSession":
        last_err = 0
        for _ in range(self._attempts):
            if _user32.OpenClipboard(None):
                self._open = True
                return self
            last_err = ctypes.get_last_error()
            time.sleep(self._delay)
        raise ClipboardError(
            f"Impossible d'ouvrir le presse-papiers (erreur Win32 {last_err})."
        )

    def __exit__(self, *exc: object) -> None:
        if self._open:
            _user32.CloseClipboard()
            self._open = False


def _read_handle_bytes(handle: int) -> bytes | None:
    """Copie le contenu d'un *handle* de mémoire globale en ``bytes``."""
    size = _kernel32.GlobalSize(handle)
    if not size:
        return None
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        _kernel32.GlobalUnlock(handle)


def _alloc_global(data: bytes) -> int:
    """Alloue un *handle* de mémoire globale et y copie ``data``."""
    size = len(data)
    handle = _kernel32.GlobalAlloc(GHND, size)
    if not handle:
        raise ClipboardError("GlobalAlloc a échoué (mémoire insuffisante).")
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        _kernel32.GlobalFree(handle)
        raise ClipboardError("GlobalLock a échoué.")
    try:
        ctypes.memmove(ptr, data, size)
    finally:
        _kernel32.GlobalUnlock(handle)
    return handle


# --------------------------------------------------------------------------- #
# API publique
# --------------------------------------------------------------------------- #
def register_format(name: str) -> int:
    """Enregistre (ou retrouve) un format de presse-papiers nommé."""
    fmt = _user32.RegisterClipboardFormatW(name)
    if not fmt:
        raise ClipboardError(f"RegisterClipboardFormat a échoué pour {name!r}.")
    return fmt


def format_name(fmt: int) -> str:
    """Nom lisible d'un format (numéro brut si non nommé) — diagnostic."""
    buf = ctypes.create_unicode_buffer(256)
    n = _user32.GetClipboardFormatNameW(fmt, buf, len(buf))
    if n > 0:
        return buf.value
    builtin = {
        CF_TEXT: "CF_TEXT", 8: "CF_DIB", CF_UNICODETEXT: "CF_UNICODETEXT",
        CF_HDROP: "CF_HDROP", 16: "CF_LOCALE", 17: "CF_DIBV5", 7: "CF_OEMTEXT",
    }
    return builtin.get(fmt, f"#{fmt}")


def snapshot() -> dict[int, bytes]:
    """Capture le contenu mémoire de **tous** les formats du presse-papiers.

    Renvoie un dictionnaire ``{format: octets}``. Les formats adossés à un
    handle non-mémoire (voir :data:`_SKIP_FORMATS`) et les formats à rendu
    différé non encore matérialisés (handle nul) sont ignorés. Un presse-papiers
    vide renvoie ``{}``.
    """
    data: dict[int, bytes] = {}
    with _ClipboardSession():
        fmt = _user32.EnumClipboardFormats(0)
        while fmt:
            if fmt not in _SKIP_FORMATS:
                handle = _user32.GetClipboardData(fmt)
                if handle:
                    buf = _read_handle_bytes(handle)
                    if buf is not None:
                        data[fmt] = buf
            fmt = _user32.EnumClipboardFormats(fmt)
    return data


def restore(data: dict[int, bytes]) -> None:
    """Restaure un instantané produit par :func:`snapshot`.

    Vide d'abord le presse-papiers puis réinjecte chaque format capturé. Un
    instantané vide (``{}``) restaure donc un presse-papiers vide — fidèle au cas
    où l'utilisateur n'avait rien copié.
    """
    with _ClipboardSession():
        if not _user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard a échoué.")
        for fmt, buf in data.items():
            handle = _alloc_global(buf)
            if not _user32.SetClipboardData(fmt, handle):
                # En cas d'échec, la propriété du handle ne passe pas au système.
                _kernel32.GlobalFree(handle)


def set_raw(fmt: int, data: bytes) -> None:
    """Place ``data`` brut sous le format ``fmt`` (vide le presse-papiers)."""
    with _ClipboardSession():
        if not _user32.EmptyClipboard():
            raise ClipboardError("EmptyClipboard a échoué.")
        handle = _alloc_global(data)
        if not _user32.SetClipboardData(fmt, handle):
            _kernel32.GlobalFree(handle)
            raise ClipboardError(f"SetClipboardData a échoué (format {fmt}).")


def set_text(text: str) -> None:
    """Écrit ``text`` comme texte Unicode (``CF_UNICODETEXT``).

    Vide le presse-papiers puis y place le texte terminé par un nul. Windows
    synthétise automatiquement les formats texte dérivés (``CF_TEXT``…).
    """
    # UTF-16-LE terminé par NUL, format attendu par CF_UNICODETEXT.
    payload = (text + "\0").encode("utf-16-le")
    set_raw(CF_UNICODETEXT, payload)


def get_text() -> str:
    """Lit le texte Unicode du presse-papiers (``""`` s'il n'y en a pas)."""
    with _ClipboardSession():
        if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)


def describe() -> list[str]:
    """Liste lisible des formats actuellement présents — diagnostic."""
    names: list[str] = []
    with _ClipboardSession():
        fmt = _user32.EnumClipboardFormats(0)
        while fmt:
            names.append(format_name(fmt))
            fmt = _user32.EnumClipboardFormats(fmt)
    return names
