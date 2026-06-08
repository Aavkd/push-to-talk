"""Banc de validation de la Phase 3 — robustesse presse-papiers / accents.

Critère de validation de la roadmap : « 20 dictées variées d'affilée (courtes,
longues, avec accents, avec presse-papiers préexistant) sans perte de texte ni
corruption du presse-papiers. »

Ce module automatise ce qui peut l'être **sans micro ni GPU** en exerçant
directement la couche presse-papiers :

- round-trip de tous les accents / caractères français ;
- préservation d'un presse-papiers texte préexistant après une « dictée » ;
- préservation d'un format **non-texte** (image / fichiers simulés) ;
- 20 cycles enchaînés rapidement sans corruption (le critère chiffré) ;
- collage de texte vide → presse-papiers intact.

Et, en option (``--live``), un test d'injection réelle au curseur : un court
compte à rebours laisse cliquer dans une fenêtre cible, puis plusieurs phrases
accentuées y sont collées via le flux complet de :func:`voix_clavier.injection.inject`.

Usage :
    python -m voix_clavier.selftest
    python -m voix_clavier.selftest --rounds 20
    python -m voix_clavier.selftest --live
"""

from __future__ import annotations

import argparse
import sys
import time

from . import clipboard
from .injection import inject

# Jeu de caractères français à valider explicitement (point de vigilance spec).
ACCENTS = "é è ê ë à â ä ç ù û ü ô î ï œ æ « » — … É À Ç Ù Ô"

# Phrases de dictée simulées : courtes, longues, accentuées, avec ponctuation.
PHRASES = [
    "Bonjour, ceci est un test de dictée vocale.",
    "Les naïfs Œdipe et Noël déjeunèrent à l'hôtel où il crût.",
    "Ça va ? J'ai hâte que tu goûtes ce thé glacé près du château.",
    ACCENTS,
    "Une phrase volontairement très longue " + "et qui continue " * 40 + "jusqu'au bout.",
    "Deuxième dictée enchaînée tout de suite après la première — sans pause.",
]


def _force_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


class _Report:
    """Petit collecteur de résultats PASS/FAIL."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def check(self, label: str, ok: bool, detail: str = "") -> bool:
        mark = "✔ PASS" if ok else "✗ ÉCHEC"
        suffix = f"  ({detail})" if detail else ""
        print(f"  {mark}  {label}{suffix}")
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        return ok

    def summary(self) -> int:
        total = self.passed + self.failed
        print("-" * 60)
        print(f"  {self.passed}/{total} vérifications réussies.")
        return 0 if self.failed == 0 else 1


# --------------------------------------------------------------------------- #
# Tests presse-papiers (sans micro)
# --------------------------------------------------------------------------- #
def _test_accents_roundtrip(rep: _Report) -> None:
    print("[1] Accents : round-trip texte via le presse-papiers")
    clipboard.set_text(ACCENTS)
    got = clipboard.get_text()
    rep.check("tous les accents préservés", got == ACCENTS,
              "" if got == ACCENTS else f"obtenu {got!r}")


def _test_text_preserved(rep: _Report) -> None:
    print("[2] Presse-papiers texte préexistant préservé après une dictée")
    original = "DONNÉE UTILISATEUR à préserver — ©€ 42"
    clipboard.set_text(original)
    before = clipboard.snapshot()
    # Simule une dictée : on écrit du texte transcrit puis on restaure.
    clipboard.set_text("texte transcrit collé")
    clipboard.restore(before)
    rep.check("texte d'origine restauré à l'identique",
              clipboard.get_text() == original)


def _test_nontext_preserved(rep: _Report) -> None:
    print("[3] Presse-papiers NON-texte (image/fichiers simulés) préservé")
    fmt = clipboard.register_format("Voix-Clavier-Test-Binaire")
    payload = bytes(range(256)) * 8  # 2 Ko de binaire arbitraire
    clipboard.set_raw(fmt, payload)
    before = clipboard.snapshot()
    # Une dictée écrase le presse-papiers avec du texte…
    clipboard.set_text("collage qui écraserait l'image")
    # … puis le restaure.
    clipboard.restore(before)
    restored = clipboard.snapshot().get(fmt)
    rep.check("contenu binaire non-texte restauré intact", restored == payload,
              "" if restored == payload else "octets divergents/absents")


def _test_empty_text(rep: _Report) -> None:
    print("[4] Transcription vide → presse-papiers intact, rien collé")
    sentinel = "NE DOIT PAS BOUGER"
    clipboard.set_text(sentinel)
    pasted = inject("", delai_restauration_ms=10)
    rep.check("inject(\"\") ne colle rien", pasted is False)
    rep.check("presse-papiers inchangé", clipboard.get_text() == sentinel)


def _test_rapid_succession(rep: _Report, rounds: int) -> None:
    print(f"[5] {rounds} cycles enchaînés rapidement, sans corruption")
    original = "CONTENU INITIAL de l'utilisateur — é à ç"
    clipboard.set_text(original)
    ok = True
    failures = 0
    for i in range(rounds):
        phrase = PHRASES[i % len(PHRASES)]
        before = clipboard.snapshot()
        clipboard.set_text(phrase)            # « collage » de la dictée
        if clipboard.get_text() != phrase:    # le texte transcrit est bien posé
            ok = False
            failures += 1
        clipboard.restore(before)             # restauration du presse-papiers
        if clipboard.get_text() != original:  # l'original revient à l'identique
            ok = False
            failures += 1
    rep.check(f"{rounds} cycles sans perte ni corruption", ok,
              "" if ok else f"{failures} écart(s)")


# --------------------------------------------------------------------------- #
# Test d'injection réelle (optionnel)
# --------------------------------------------------------------------------- #
def _test_live_injection(countdown: float, delay_ms: int) -> None:
    print("=" * 60)
    print(" Test d'injection RÉELLE au curseur")
    print(" Cliquez dans une fenêtre de saisie (éditeur, navigateur, IA…).")
    print("=" * 60)
    original = "Presse-papiers utilisateur d'origine (à retrouver après le test)"
    clipboard.set_text(original)
    print(f"  Presse-papiers préchargé avec : {original!r}")

    for n in range(int(countdown), 0, -1):
        print(f"  Collage dans {n}s — cliquez MAINTENANT dans la fenêtre cible…")
        time.sleep(1)

    for idx, phrase in enumerate(PHRASES, 1):
        inject(phrase, delai_restauration_ms=delay_ms)
        print(f"  [{idx}/{len(PHRASES)}] collé : {phrase[:60]}…")
        time.sleep(0.4)

    final = clipboard.get_text()
    print("-" * 60)
    if final == original:
        print("  ✔ Presse-papiers d'origine correctement restauré après les collages.")
    else:
        print(f"  ✗ Presse-papiers NON restauré : {final!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voix-clavier-selftest",
        description="Validation Phase 3 : robustesse presse-papiers et accents.",
    )
    parser.add_argument(
        "--rounds", type=int, default=20,
        help="Nombre de cycles enchaînés pour le test de succession (défaut : 20).",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Lance en plus le test d'injection réelle au curseur.",
    )
    parser.add_argument(
        "--countdown", type=float, default=4.0,
        help="Compte à rebours (s) avant le collage en mode --live (défaut : 4).",
    )
    parser.add_argument(
        "--delay-ms", type=int, default=80,
        help="Délai de restauration du presse-papiers pour --live (défaut : 80).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_console()
    args = build_parser().parse_args(argv)

    if sys.platform != "win32":
        print("Ce banc de test cible Windows (API presse-papiers Win32).", file=sys.stderr)
        return 2

    print("=" * 60)
    print(" Voix → Clavier — validation Phase 3 (presse-papiers / accents)")
    print("=" * 60)

    # Préserver puis restaurer le vrai presse-papiers de l'utilisateur autour
    # des tests, pour ne pas le perturber.
    user_clip = clipboard.snapshot()
    rep = _Report()
    try:
        _test_accents_roundtrip(rep)
        _test_text_preserved(rep)
        _test_nontext_preserved(rep)
        _test_empty_text(rep)
        _test_rapid_succession(rep, max(1, args.rounds))
    finally:
        try:
            clipboard.restore(user_clip)
        except Exception:  # noqa: BLE001
            pass

    code = rep.summary()

    if args.live:
        _test_live_injection(args.countdown, args.delay_ms)

    return code


if __name__ == "__main__":
    raise SystemExit(main())
