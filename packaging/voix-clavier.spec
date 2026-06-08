# -*- mode: python ; coding: utf-8 -*-
"""Spécification PyInstaller — Voix → Clavier (Phase 8).

Construit un exécutable Windows **onedir** (un dossier distribuable). Le mode
onedir est préféré au onefile pour cette application : les dépendances natives
sont lourdes (CTranslate2 + CUDA/cuDNN + PyAV + PySide6) et un onefile les
ré-extrait dans un dossier temporaire à chaque lancement, ce qui rallonge
sensiblement le démarrage.

Le modèle Whisper n'est **pas** embarqué (large-v3 ≈ 3 Go) : il est téléchargé au
premier lancement avec retour visuel (voir voix_clavier.models / ui.download) et
mis en cache sous %LOCALAPPDATA%\\VoixClavier\\models.

Build :
    pyinstaller packaging/voix-clavier.spec --noconfirm
ou via packaging/build.ps1.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# Racine du projet (le .spec est dans packaging/).
ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"

# --- Dépendances natives lourdes : binaires + données + sous-modules --------- #
datas = []
binaries = []
hiddenimports = []

for pkg in ("ctranslate2", "faster_whisper", "av", "sounddevice"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # pragma: no cover - paquet absent au build
        print(f"[spec] collect_all({pkg}) ignoré : {exc}")

# Backends à imports dynamiques (chargés par nom à l'exécution).
hiddenimports += collect_submodules("pynput")
hiddenimports += collect_submodules("pystray")
hiddenimports += [
    "huggingface_hub",
    "tqdm",
    "tomli_w",
    "PIL",
    "win32timezone",  # parfois requis par les backends Windows
]

# --- DLL CUDA / cuDNN (paquets pip nvidia-*-cu12) ---------------------------- #
# Placées à la racine d'extraction ('.') ; le runtime hook enregistre ce dossier
# auprès du loader Windows pour que CTranslate2 les trouve.
for entry in sys.path:
    nvidia_root = Path(entry) / "nvidia"
    if nvidia_root.is_dir():
        for dll in nvidia_root.glob("*/bin/*.dll"):
            binaries.append((str(dll), "."))

# --- config.toml par défaut embarqué (graine au 1er lancement) --------------- #
config_default = ROOT / "config.toml"
if config_default.exists():
    datas.append((str(config_default), "."))

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "runtime_hook_cuda.py")],
    excludes=["tkinter", "matplotlib", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoixClavier",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=False : pas de fenêtre console (l'app vit dans le systray / la
    # pilule). Les diagnostics passent par le journal et par VoixClavier-doctor.
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VoixClavier",
)
