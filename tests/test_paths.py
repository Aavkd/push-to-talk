"""Tests d'intégration Phase 8 — résolution des chemins (dev vs packagé)."""

from __future__ import annotations

from voix_clavier import paths


def test_dev_mode_uses_project_root(monkeypatch):
    monkeypatch.delattr(paths.sys, "frozen", raising=False)
    assert paths.is_frozen() is False
    assert paths.config_path() == paths.project_root() / "config.toml"
    # En dev, le cache modèle reste le cache Hugging Face par défaut.
    assert paths.model_cache_dir() is None


def test_frozen_config_in_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert paths.is_frozen() is True
    expected = tmp_path / "VoixClavier" / "config.toml"
    assert paths.config_path() == expected
    # Cache modèle dans le dossier de données utilisateur, créé à la demande.
    cache = paths.model_cache_dir()
    assert cache == tmp_path / "VoixClavier" / "models"
    assert cache.is_dir()


def test_seed_user_config_copies_template(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    template = tmp_path / "bundle" / "config.toml"
    template.parent.mkdir(parents=True)
    template.write_text('[declenchement]\nmode = "toggle"\n', encoding="utf-8")
    monkeypatch.setattr(paths, "bundle_dir", lambda: template.parent)

    seeded = paths.seed_user_config()
    assert seeded.exists()
    assert "toggle" in seeded.read_text(encoding="utf-8")

    # Idempotent : un second appel ne réécrit pas par-dessus une config existante.
    seeded.write_text('[declenchement]\nmode = "push-to-talk"\n', encoding="utf-8")
    paths.seed_user_config()
    assert "push-to-talk" in seeded.read_text(encoding="utf-8")
