"""Présence et téléchargement du modèle Whisper, avec retour visuel (Phase 8).

``large-v3`` pèse ~3 Go : au **premier lancement** d'une installation packagée,
il faut le télécharger, et l'utilisateur doit en avoir un retour (sinon
l'application semble figée pendant plusieurs minutes). Ce module :

- détecte si un modèle est déjà en cache (:func:`is_model_cached`) ;
- le télécharge sinon (:func:`ensure_model`) en rapportant la progression via un
  callback, branché sur la console ou sur une fenêtre Qt (voir
  :mod:`voix_clavier.ui.download`).

faster-whisper télécharge ses modèles depuis le Hub Hugging Face. On reproduit
exactement la même destination de cache (paramètre ``cache_dir`` /
``download_root``) pour qu'après ce pré-téléchargement, le chargement par
:class:`~voix_clavier.transcription.Transcriber` ne re-télécharge rien.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .logsetup import get_logger

_log = get_logger("models")

# Rapport de progression : (libellé_fichier, fraction_0_à_1 | None).
# ``fraction=None`` signale une étape indéterminée (préparation / vérification).
ProgressCallback = Callable[[str, "float | None"], None]


def repo_id(model: str) -> str:
    """Identifiant de dépôt Hub pour un nom de modèle faster-whisper.

    ``"large-v3"`` → ``"Systran/faster-whisper-large-v3"``. Un identifiant déjà
    complet (contenant ``"/"``) ou un chemin local est renvoyé tel quel.
    """
    if "/" in model or Path(model).exists():
        return model
    try:
        from faster_whisper.utils import _MODELS  # type: ignore[attr-defined]

        if model in _MODELS:
            return _MODELS[model]
    except Exception:  # noqa: BLE001 - table interne absente / renommée : repli
        pass
    # Convention historique des dépôts faster-whisper.
    return f"Systran/faster-whisper-{model}"


def is_model_cached(model: str, cache_dir: "str | Path | None") -> bool:
    """Vrai si ``model`` est déjà présent dans le cache (aucun réseau requis)."""
    try:
        from faster_whisper.utils import download_model

        download_model(
            model,
            local_files_only=True,
            cache_dir=str(cache_dir) if cache_dir is not None else None,
        )
        return True
    except Exception:  # noqa: BLE001 - absent du cache (ou backend indisponible)
        return False


def _make_tqdm(progress: ProgressCallback):
    """Fabrique une sous-classe ``tqdm`` qui relaie l'avancement au callback.

    ``huggingface_hub`` télécharge fichier par fichier, chacun avec sa propre
    barre ``tqdm``. On rapporte donc la progression **du fichier courant** (nom +
    fraction), ce qui suffit comme retour visuel pour un téléchargement unique.
    """
    import sys
    from tqdm.auto import tqdm as _base_tqdm

    class _NullWriter:
        """Flux muet pour tqdm quand il n'y a pas de console (console=False packagé)."""
        def write(self, s: str) -> int: return 0
        def flush(self) -> None: pass

    # En mode packagé sans console, sys.stderr est None : tqdm planterait sur write.
    _sink = _NullWriter() if (sys.stderr is None or sys.stdout is None) else None

    class _ReportingTqdm(_base_tqdm):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            if _sink is not None:
                kwargs.setdefault("file", _sink)
            super().__init__(*args, **kwargs)

        def update(self, n: float | None = 1):  # noqa: D401 - surcharge tqdm
            result = super().update(n)
            try:
                label = str(self.desc or "modèle").strip().rstrip(":")
                frac = (self.n / self.total) if self.total else None
                progress(label, frac)
            except Exception:  # noqa: BLE001 - le rapport ne doit jamais planter le download
                pass
            return result

    return _ReportingTqdm


def ensure_model(
    model: str,
    cache_dir: "str | Path | None" = None,
    progress: ProgressCallback | None = None,
) -> str:
    """Garantit la présence locale de ``model`` ; le télécharge au besoin.

    Renvoie le chemin local du modèle. Si ``progress`` est fourni et qu'un
    téléchargement a effectivement lieu, il est appelé au fil de l'avancement.
    Lève en cas d'échec réseau **et** d'absence de cache (l'appelant décide alors
    du repli — p. ex. modèle léger en CPU).
    """
    cache = str(cache_dir) if cache_dir is not None else None

    if is_model_cached(model, cache_dir):
        if progress is not None:
            progress("déjà en cache", 1.0)
        return _cached_path(model, cache)

    rid = repo_id(model)
    _log.info("Téléchargement du modèle '%s' (%s) → cache=%s", model, rid, cache or "défaut")
    if progress is not None:
        progress("préparation", None)

    import sys

    from huggingface_hub import snapshot_download

    # Sans console (packagé, console=False), sys.stderr est None : tqdm planterait.
    # On désactive les barres HF et on passe uniquement notre tqdm muet.
    no_console = sys.stderr is None or sys.stdout is None

    kwargs: dict[str, object] = {"repo_id": rid}
    if cache is not None:
        kwargs["cache_dir"] = cache
    if no_console:
        # Désactive toutes les barres de progression HF (y compris celles que
        # snapshot_download crée en dehors de tqdm_class).
        kwargs["local_files_only"] = False  # déjà False par défaut, explicite
        import os
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    if progress is not None:
        kwargs["tqdm_class"] = _make_tqdm(progress)

    local = snapshot_download(**kwargs)  # type: ignore[arg-type]
    if progress is not None:
        progress("terminé", 1.0)
    _log.info("Modèle '%s' disponible dans %s", model, local)
    return local


def _cached_path(model: str, cache: "str | None") -> str:
    """Chemin local d'un modèle déjà en cache (best-effort, pour information)."""
    try:
        from faster_whisper.utils import download_model

        return download_model(model, local_files_only=True, cache_dir=cache)
    except Exception:  # noqa: BLE001
        return model
