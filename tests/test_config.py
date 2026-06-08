"""Tests d'intégration Phase 8 — round-trip de la configuration."""

from __future__ import annotations

from voix_clavier.config import Config, load_config, save_config


def test_defaults_match_decisions():
    """Les valeurs par défaut respectent les décisions arrêtées de la roadmap."""
    c = Config()
    assert c.mode == "push-to-talk"
    assert c.raccourci == "ctrl+space"
    assert c.modele == "large-v3"
    assert c.device == "cuda"
    assert c.compute_type == "float16"
    assert c.variante_pilule == "D"


def test_langue_whisper_autodetect():
    assert Config(langue="fr").langue_whisper == "fr"
    assert Config(langue="").langue_whisper is None
    assert Config(langue="auto").langue_whisper is None
    assert Config(langue="  AUTO ").langue_whisper is None


def test_save_then_load_roundtrip(tmp_path):
    """Écrire puis relire la config restitue exactement les mêmes valeurs."""
    path = tmp_path / "config.toml"
    original = Config(
        mode="toggle",
        raccourci="ctrl+alt+s",
        modele="small",
        device="cpu",
        compute_type="int8",
        langue="en",
        peripherique="Micro USB",
        delai_restauration_ms=120,
        variante_pilule="C",
        position_pilule="centre",
        lancer_au_demarrage=True,
        afficher_systray=False,
    )
    save_config(original, path)
    reloaded = load_config(path)
    assert reloaded.to_dict() == original.to_dict()


def test_load_missing_file_returns_defaults(tmp_path):
    """Un fichier absent ne lève pas : on retombe sur les valeurs par défaut."""
    reloaded = load_config(tmp_path / "absent.toml")
    assert reloaded.to_dict() == Config().to_dict()
