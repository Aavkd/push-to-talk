"""Tests d'intégration Phase 8 — machine à états (cycle nominal + cas limites)."""

from __future__ import annotations

import pytest

from voix_clavier.state import IllegalTransition, State, StateMachine


def test_nominal_cycle():
    m = StateMachine()
    assert m.state is State.REPOS
    m.to(State.ECOUTE)
    m.to(State.TRANSCRIPTION)
    m.to(State.INJECTION)
    m.to(State.REPOS)
    assert m.state is State.REPOS


def test_illegal_transition_raises():
    m = StateMachine()
    with pytest.raises(IllegalTransition):
        m.to(State.INJECTION)  # Repos → Injection interdit


def test_empty_transcription_back_to_repos():
    """Transcription vide : Transcription → Repos directement (rien à coller)."""
    m = StateMachine()
    m.to(State.ECOUTE)
    m.to(State.TRANSCRIPTION)
    assert m.can(State.REPOS)
    m.to(State.REPOS)
    assert m.state is State.REPOS


def test_error_then_reset():
    m = StateMachine()
    m.to(State.ECOUTE)
    m.to_error()
    assert m.state is State.ERREUR
    # Seule sortie de l'erreur : retour au repos.
    assert m.can(State.REPOS)
    m.reset()
    assert m.state is State.REPOS


def test_listeners_notified():
    transitions: list[tuple[State, State]] = []
    m = StateMachine(on_change=lambda old, new: transitions.append((old, new)))
    m.subscribe(lambda old, new: transitions.append((old, new)))
    m.to(State.ECOUTE)
    # Les deux observateurs sont notifiés de la même transition.
    assert transitions == [(State.REPOS, State.ECOUTE), (State.REPOS, State.ECOUTE)]
