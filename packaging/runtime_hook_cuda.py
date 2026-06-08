"""Runtime hook PyInstaller (Phase 8) : rendre les DLL CUDA/cuDNN trouvables.

Exécuté **avant** le code applicatif, donc avant tout import de CTranslate2. Les
DLL ``cublas64_12.dll`` / ``cudnn*.dll`` sont embarquées à la racine d'extraction
(``_MEIPASS``) par le ``.spec`` ; on enregistre ce dossier auprès du loader Windows
pour que CTranslate2 les charge par leur nom.

On évite d'importer le package applicatif ici (l'environnement gelé n'est pas
encore totalement initialisé) : la logique minimale est dupliquée volontairement.
"""

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    base = getattr(sys, "_MEIPASS", None)
    if base:
        roots = [Path(base)]
        nvidia = Path(base) / "nvidia"
        if nvidia.is_dir():
            roots.extend(nvidia.glob("*/bin"))
        for d in roots:
            if d.is_dir():
                try:
                    os.add_dll_directory(str(d))
                except (OSError, FileNotFoundError):
                    pass
                if str(d) not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
