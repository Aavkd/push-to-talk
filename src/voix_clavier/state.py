"""Machine à états explicite de la dictée (Phase 2).

États : ``Repos → Écoute → Transcription → Injection → Repos``, plus un état
``Erreur`` accessible depuis n'importe où et qui ne peut que revenir à ``Repos``.

La :class:`StateMachine` valide les transitions (les transitions illégales lèvent
:class:`IllegalTransition`) et notifie des observateurs à chaque changement. Cela
permet à la boucle de dictée (Phase 2), puis au systray (Phase 4) et à la pilule
(Phase 5), de réagir à l'état sans se coupler entre eux.

La machine est protégée par un verrou réentrant : les callbacks (raccourci,
worker de transcription) viennent de threads différents.
"""

from __future__ import annotations

import threading
from enum import Enum, auto
from typing import Callable


class State(Enum):
    REPOS = auto()
    ECOUTE = auto()
    TRANSCRIPTION = auto()
    INJECTION = auto()
    ERREUR = auto()


# Libellés lisibles pour le feedback console (Phase 1/2) et, plus tard, l'UI.
LABELS: dict["State", str] = {
    State.REPOS: "Repos",
    State.ECOUTE: "Écoute",
    State.TRANSCRIPTION: "Transcription",
    State.INJECTION: "Injection",
    State.ERREUR: "Erreur",
}


# Transitions autorisées. Le cycle nominal est
# Repos → Écoute → Transcription → Injection → Repos ; on autorise aussi les
# raccourcis légitimes :
#   - Écoute → Repos          : dictée annulée / buffer vide (silence).
#   - Transcription → Repos   : transcription vide, rien à coller.
#   - * → Erreur              : toute étape peut échouer.
#   - Erreur → Repos          : seule sortie de l'erreur.
_ALLOWED: dict[State, frozenset[State]] = {
    State.REPOS: frozenset({State.ECOUTE, State.ERREUR}),
    State.ECOUTE: frozenset({State.TRANSCRIPTION, State.REPOS, State.ERREUR}),
    State.TRANSCRIPTION: frozenset({State.INJECTION, State.REPOS, State.ERREUR}),
    State.INJECTION: frozenset({State.REPOS, State.ERREUR}),
    State.ERREUR: frozenset({State.REPOS}),
}


class IllegalTransition(RuntimeError):
    """Levée quand on tente une transition non autorisée par la machine."""

    def __init__(self, current: State, target: State) -> None:
        super().__init__(
            f"Transition illégale : {LABELS[current]} → {LABELS[target]}"
        )
        self.current = current
        self.target = target


# Callback notifié à chaque changement d'état : (ancien, nouveau).
StateListener = Callable[[State, State], None]


class StateMachine:
    """Machine à états thread-safe avec validation et notification."""

    def __init__(self, on_change: StateListener | None = None) -> None:
        self._state = State.REPOS
        self._lock = threading.RLock()
        self._listeners: list[StateListener] = []
        if on_change is not None:
            self._listeners.append(on_change)

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def subscribe(self, listener: StateListener) -> None:
        """Ajoute un observateur notifié à chaque transition."""
        with self._lock:
            self._listeners.append(listener)

    def can(self, target: State) -> bool:
        """Indique si la transition vers ``target`` est autorisée actuellement."""
        with self._lock:
            return target in _ALLOWED[self._state]

    def to(self, target: State) -> State:
        """Effectue la transition vers ``target``.

        Lève :class:`IllegalTransition` si elle n'est pas autorisée. Les
        observateurs sont notifiés **hors verrou** pour éviter tout interblocage
        si un callback réinterroge la machine.
        """
        with self._lock:
            current = self._state
            if target not in _ALLOWED[current]:
                raise IllegalTransition(current, target)
            self._state = target
            listeners = tuple(self._listeners)

        for listener in listeners:
            listener(current, target)
        return target

    def to_error(self) -> State:
        """Bascule en :data:`State.ERREUR` depuis n'importe quel état (idempotent)."""
        with self._lock:
            current = self._state
            if current is State.ERREUR:
                return current
            self._state = State.ERREUR
            listeners = tuple(self._listeners)

        for listener in listeners:
            listener(current, State.ERREUR)
        return State.ERREUR

    def reset(self) -> State:
        """Force le retour à :data:`State.REPOS` (récupération après erreur/annulation)."""
        with self._lock:
            current = self._state
            if current is State.REPOS:
                return current
            self._state = State.REPOS
            listeners = tuple(self._listeners)

        for listener in listeners:
            listener(current, State.REPOS)
        return State.REPOS
