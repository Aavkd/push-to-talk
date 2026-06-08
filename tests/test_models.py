"""Tests d'intégration Phase 8 — résolution de dépôt et progression du modèle.

Aucun réseau : on vérifie la logique de :mod:`voix_clavier.models` sans
télécharger (faster-whisper / huggingface_hub sont sollicités de façon contrôlée).
"""

from __future__ import annotations

from voix_clavier import models


def test_repo_id_known_names():
    # Convention des dépôts faster-whisper ; tolère l'absence de la table interne.
    assert models.repo_id("large-v3").endswith("faster-whisper-large-v3")
    assert models.repo_id("tiny").endswith("faster-whisper-tiny")


def test_repo_id_passthrough_full_id():
    assert models.repo_id("Systran/faster-whisper-medium") == "Systran/faster-whisper-medium"


def test_is_model_cached_false_when_backend_missing(monkeypatch):
    """Sans backend / sans cache, on rapporte « non mis en cache » sans lever."""

    def boom(*_a, **_k):
        raise RuntimeError("pas de cache")

    # Patch la fonction telle qu'importée paresseusement.
    import faster_whisper.utils as fwu  # type: ignore

    monkeypatch.setattr(fwu, "download_model", boom, raising=False)
    assert models.is_model_cached("large-v3", None) is False


def test_reporting_tqdm_calls_progress():
    """La sous-classe tqdm relaie l'avancement (label, fraction) au callback."""
    reports: list[tuple[str, float | None]] = []
    Tqdm = models._make_tqdm(lambda label, frac: reports.append((label, frac)))

    bar = Tqdm(total=10, desc="model.bin")
    bar.update(5)
    bar.update(5)
    bar.close()

    assert reports, "le callback de progression doit être appelé"
    last_label, last_frac = reports[-1]
    assert last_label == "model.bin"
    assert last_frac == 1.0
