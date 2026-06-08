"""Génération des icônes systray (Phase 4, Section 4 des wireframes).

Quatre visuels, un par état pertinent de la machine à états :

- **repos**      : micro gris (au repos, prêt).
- **écoute**     : micro rouge (capture en cours).
- **traitement** : anneau/spinner bleu (transcription en cours).
- **erreur**     : triangle d'avertissement orange.

Les icônes sont dessinées avec Pillow (aucun fichier binaire à embarquer) à une
résolution confortable (64×64) ; ``pystray`` les redimensionne pour la barre des
tâches (16×16 / 32×32). Le fond est transparent pour s'intégrer aux thèmes clair
et sombre de Windows.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..state import State

# Taille de dessin ; Windows réduit ensuite à 16/32 px.
_SIZE = 64

# Couleurs reprises des wireframes (Section 4).
_GREY = (150, 150, 150, 255)     # repos  (#969696, proche du #bbb/#888 du design)
_RED = (238, 0, 51, 255)         # écoute (#e03)
_BLUE = (34, 102, 238, 255)      # traitement (#2266ee)
_ORANGE = (238, 136, 0, 255)     # erreur (#e80)


def _new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (_SIZE, _SIZE), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _draw_mic(draw: ImageDraw.ImageDraw, color: tuple[int, int, int, int]) -> None:
    """Dessine un micro stylisé (capsule + arceau + pied) centré."""
    # Capsule (corps du micro).
    cx = _SIZE / 2
    cap_w, cap_top, cap_bot = 22, 12, 38
    draw.rounded_rectangle(
        (cx - cap_w / 2, cap_top, cx + cap_w / 2, cap_bot),
        radius=cap_w / 2,
        fill=color,
    )
    # Arceau (support en U sous la capsule).
    draw.arc((cx - 18, cap_bot - 18, cx + 18, cap_bot + 10), start=0, end=180,
             fill=color, width=4)
    # Pied vertical + base.
    draw.line((cx, cap_bot + 10, cx, 54), fill=color, width=4)
    draw.line((cx - 12, 54, cx + 12, 54), fill=color, width=4)


def _draw_spinner(draw: ImageDraw.ImageDraw, color: tuple[int, int, int, int]) -> None:
    """Dessine un anneau ouvert (spinner) évoquant le traitement."""
    box = (14, 14, _SIZE - 14, _SIZE - 14)
    # Arc des trois quarts : la « bouche » ouverte suggère la rotation.
    draw.arc(box, start=45, end=315, fill=color, width=7)


def _draw_warning(draw: ImageDraw.ImageDraw, color: tuple[int, int, int, int]) -> None:
    """Dessine un triangle d'avertissement avec un point d'exclamation."""
    cx = _SIZE / 2
    draw.polygon([(cx, 10), (_SIZE - 10, 54), (10, 54)], outline=color, width=4)
    # Barre du « ! ».
    draw.line((cx, 26, cx, 42), fill=color, width=4)
    # Point du « ! ».
    draw.ellipse((cx - 2, 46, cx + 2, 50), fill=color)


def make_icon(state: State) -> Image.Image:
    """Construit l'image d'icône correspondant à ``state``.

    Les états ``TRANSCRIPTION`` et ``INJECTION`` partagent le visuel
    « traitement » (spinner bleu) : du point de vue de l'utilisateur, l'app
    travaille dans les deux cas.
    """
    img, draw = _new_canvas()
    if state is State.ECOUTE:
        _draw_mic(draw, _RED)
    elif state in (State.TRANSCRIPTION, State.INJECTION):
        _draw_spinner(draw, _BLUE)
    elif state is State.ERREUR:
        _draw_warning(draw, _ORANGE)
    else:  # State.REPOS et tout cas par défaut
        _draw_mic(draw, _GREY)
    return img


# Pré-rendu des quatre visuels (les icônes ne changent jamais une fois créées).
_CACHE: dict[State, Image.Image] = {}


def icon_for(state: State) -> Image.Image:
    """Renvoie l'icône (mise en cache) pour ``state``."""
    key = state
    if state in (State.TRANSCRIPTION, State.INJECTION):
        key = State.TRANSCRIPTION  # visuel partagé
    if key not in _CACHE:
        _CACHE[key] = make_icon(state)
    return _CACHE[key]
