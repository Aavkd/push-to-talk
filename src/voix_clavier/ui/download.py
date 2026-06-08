"""Fenêtre de progression du téléchargement du modèle au 1er lancement (Phase 8).

``large-v3`` pèse ~3 Go : sur une installation packagée fraîche (sans console
visible), il faut un **retour visuel** pendant le téléchargement, sinon
l'application paraît figée. Cette petite fenêtre Qt affiche l'avancement rapporté
par :func:`voix_clavier.models.ensure_model`.

Le téléchargement tourne dans un thread de travail ; un ``QTimer`` rafraîchit la
barre sur le thread GUI à partir d'un état partagé (écritures atomiques protégées
par le GIL — pas de structure de synchronisation lourde nécessaire pour un simple
affichage).

La ``QApplication`` créée ici est réutilisée ensuite par la pilule (Phase 5),
qui récupère l'instance existante via ``QApplication.instance()``.
"""

from __future__ import annotations

import threading

from .. import models, paths
from ..config import Config
from ..logsetup import get_logger

_log = get_logger("ui.download")


def ensure_model_with_dialog(config: Config) -> bool:
    """Télécharge le modèle avec une fenêtre de progression Qt.

    Renvoie ``True`` si le modèle est disponible à la sortie, ``False`` si les
    dépendances Qt manquent, si l'utilisateur annule, ou en cas d'échec — auquel
    cas l'appelant se rabat sur la console (puis sur le repli de chargement).
    """
    try:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import QApplication, QProgressDialog
    except ImportError:
        return False

    cache_dir = paths.model_cache_dir()

    # État partagé entre le worker et le thread GUI (lectures/écritures simples).
    shared: dict[str, object] = {
        "label": "Préparation…",
        "frac": None,
        "done": False,
        "ok": False,
        "error": None,
    }
    cancel = threading.Event()

    def on_progress(label: str, frac: float | None) -> None:
        if cancel.is_set():
            # Interrompt le téléchargement coopérativement.
            raise KeyboardInterrupt("Téléchargement annulé par l'utilisateur.")
        shared["label"] = label
        shared["frac"] = frac

    def worker() -> None:
        try:
            models.ensure_model(config.modele, cache_dir, progress=on_progress)
            shared["ok"] = True
        except KeyboardInterrupt:
            shared["ok"] = False
        except Exception as exc:  # noqa: BLE001 - rapporté à l'appelant via l'état
            shared["error"] = str(exc)
            shared["ok"] = False
            _log.warning("Téléchargement du modèle échoué : %s", exc)
        finally:
            shared["done"] = True

    qapp = QApplication.instance() or QApplication([])

    dialog = QProgressDialog(
        f"Téléchargement du modèle « {config.modele} »…\n"
        "Premier lancement uniquement (~3 Go pour large-v3).",
        "Annuler",
        0,
        100,
    )
    dialog.setWindowTitle("Voix → Clavier — préparation")
    dialog.setWindowModality(Qt.ApplicationModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.canceled.connect(cancel.set)

    thread = threading.Thread(target=worker, name="model-download", daemon=True)
    thread.start()

    timer = QTimer()

    def tick() -> None:
        if shared["done"]:
            timer.stop()
            dialog.close()
            qapp.quit()
            return
        label = str(shared["label"])
        frac = shared["frac"]
        dialog.setLabelText(
            f"Téléchargement du modèle « {config.modele} »…\n{label}"
        )
        if frac is None:
            # Étape indéterminée : barre en mode « occupé ».
            dialog.setRange(0, 0)
        else:
            dialog.setRange(0, 100)
            dialog.setValue(int(float(frac) * 100))

    timer.timeout.connect(tick)
    timer.start(120)
    dialog.show()
    qapp.exec()

    # Laisse le worker se terminer proprement après annulation.
    thread.join(timeout=2.0)
    if shared["error"]:
        print(f"[modèle] téléchargement impossible : {shared['error']}")
    return bool(shared["ok"])
