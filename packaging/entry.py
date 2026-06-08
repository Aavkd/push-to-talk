"""Point d'entrée pour l'exécutable packagé (Phase 8).

PyInstaller a besoin d'un *script* (et non d'un module à imports relatifs) comme
point de départ. On délègue immédiatement à :func:`voix_clavier.app.main`.
"""

import sys

from voix_clavier.app import main

if __name__ == "__main__":
    sys.exit(main())
