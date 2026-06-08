"""Pilule flottante always-on-top — Phase 5 (Section 1 des wireframes).

Petite fenêtre sans bordure, toujours au-dessus des autres applications, à fond
translucide, repositionnable par glisser et persistante en position. Elle reflète
en temps réel la machine à états de la dictée (Phase 2) :

- **repos**          : pilule quasi-invisible (contour pointillé, mic gris) ;
- **écoute**         : fond rouge plein + timer d'enregistrement + waveform
  réagissant au volume réel du micro (:pyattr:`Recorder.level`) ;
- **transcription**  : fond bleu plein + spinner rotatif + « traitement… » ;
- **erreur**         : fond orange bref avant retour au repos.

Architecture interchangeable : le rendu est dispatché par identifiant de variante
(:data:`PILL_VARIANTS`) et la fabrique :func:`make_pill` lit
``config.variante_pilule``. Les quatre variantes des wireframes sont réalisées :

- **D** — pilule complète (par défaut) : aplats pleins, timer + waveform ;
- **A** — barre minimale : point coloré + libellé, point rouge pulsé en écoute ;
- **B** — forme d'onde : micro + barres réagissant au volume + spinner ;
- **C** — badge circulaire ~50×50 : anneau coloré + halo pulsé, anneau tournant.

Chaque variante a sa propre géométrie (:data:`_VARIANT_SIZES`) ; changer de
variante à chaud redimensionne et repositionne la fenêtre.

Threading : la machine à états notifie depuis les threads du moteur (écoute
clavier, worker de transcription). :class:`FloatingPill` reçoit ces transitions
via un signal Qt en connexion *queued*, ce qui garantit que toute manipulation du
widget se fait sur le thread GUI. Un :class:`QTimer` anime le timer, la waveform,
la pulsation et le spinner sur ce même thread.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from ..state import State

# --- Palette (reprise de la Section 1 des wireframes) ----------------------- #
_RED = QColor("#ee0033")        # écoute
_BLUE = QColor("#2266ee")       # transcription
_ORANGE = QColor("#ee8800")     # erreur
_GREY = QColor("#bbbbbb")       # repos (contour + texte)
_WHITE = QColor("#ffffff")

# Fond « fantôme » du repos, repris de la variante D : quasi transparent pour ne
# jamais éblouir (les fonds pastel clairs sont proscrits — ils sont surexposés en
# HDR). Les états actifs utilisent des aplats pleins saturés + contenu blanc.
_GHOST_BG = QColor(248, 247, 244, 36)

# Géométrie de la fenêtre selon la variante. A/B/D sont des barres, C un badge
# circulaire compact (« ~50×50px » dans les wireframes).
_VARIANT_SIZES: dict[str, tuple[int, int]] = {
    "A": (150, 40),
    "B": (210, 48),
    "C": (52, 52),
    "D": (210, 48),
}

# Nombre de barres de la waveform et fenêtre d'historique des niveaux.
_WAVE_BARS = 7
# Gain appliqué au RMS du micro (les niveaux de parole sont faibles, ~0.02-0.1).
_LEVEL_GAIN = 9.0

# Période d'animation (ms). 50 ms ≈ 20 FPS : fluide pour la waveform/spinner,
# négligeable pour le CPU au repos.
_ANIM_MS = 50


class FloatingPill(QWidget):
    """Fenêtre flottante variante D, pilotée par l'état de la dictée.

    Le moteur appelle :meth:`set_state` depuis un autre thread ; l'appel est
    réémis vers le thread GUI par le signal :data:`_state_signal`.
    """

    # Signal interne pour marshaler une transition vers le thread GUI.
    _state_signal = Signal(object)

    def __init__(
        self,
        *,
        variant: str = "D",
        anchor: str = "bas-droite",
        level_provider: Callable[[], float] | None = None,
        on_quit: Callable[[], None] | None = None,
        on_settings: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(None)
        self._on_quit = on_quit
        self._on_settings = on_settings
        self._variant = (variant or "D").strip().upper()
        if self._variant not in PILL_VARIANTS:
            # Variantes A/B/C différées : on retombe proprement sur D.
            self._variant = "D"
        self._anchor = anchor
        self._level_provider = level_provider or (lambda: 0.0)

        self._state = State.REPOS
        self._listen_started = 0.0          # monotonic du début d'écoute (timer)
        self._spinner_angle = 0.0           # degrés, pour le spinner de transcription
        self._pulse_phase = 0.0             # phase de pulsation en écoute
        self._levels: deque[float] = deque([0.0] * _WAVE_BARS, maxlen=_WAVE_BARS)

        # Position de glisser : décalage curseur ↔ coin de la fenêtre.
        self._drag_offset: QPoint | None = None
        self._settings = QSettings("MANTARA", "VoixClavier")

        self._init_window()
        self._restore_position()

        self._state_signal.connect(self._apply_state, Qt.QueuedConnection)

        self._timer = QTimer(self)
        self._timer.setInterval(_ANIM_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ------------------------------------------------------------------ #
    # Fenêtre : sans bordure, always-on-top, translucide, hors barre des tâches
    # ------------------------------------------------------------------ #
    def _init_window(self) -> None:
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                 # pas d'entrée dans la barre des tâches
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._apply_variant_size()
        self.setWindowTitle("Voix → Clavier")
        # Police « manuscrite » des wireframes si disponible, sinon repli système.
        self._font = QFont("Segoe UI", 11, QFont.DemiBold)

    def _apply_variant_size(self) -> None:
        """Fixe la taille de la fenêtre selon la variante courante."""
        w, h = _VARIANT_SIZES.get(self._variant, _VARIANT_SIZES["D"])
        self.setFixedSize(w, h)

    # ------------------------------------------------------------------ #
    # Positionnement (anchor par défaut + override de glisser persistant)
    # ------------------------------------------------------------------ #
    def _anchor_position(self) -> QPoint:
        """Calcule la position d'ancrage sur l'écran courant (ou principal)."""
        screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        margin = 24
        w, h = self.width(), self.height()
        a = (self._anchor or "bas-droite").strip().lower()
        if a in ("bas-droite", "bottom-right"):
            x, y = geo.right() - w - margin, geo.bottom() - h - margin
        elif a in ("bas-gauche", "bottom-left"):
            x, y = geo.left() + margin, geo.bottom() - h - margin
        elif a in ("haut-droite", "top-right"):
            x, y = geo.right() - w - margin, geo.top() + margin
        elif a in ("haut-gauche", "top-left"):
            x, y = geo.left() + margin, geo.top() + margin
        else:  # centre
            x = geo.left() + (geo.width() - w) // 2
            y = geo.top() + (geo.height() - h) // 2
        return QPoint(int(x), int(y))

    def _restore_position(self) -> None:
        """Restaure la position glissée si elle reste visible, sinon l'ancrage.

        Multi-écrans : une position sauvegardée qui tombe hors de la zone visible
        virtuelle (écran débranché, casque Quest 3 retiré) est ignorée au profit
        de l'ancrage sur l'écran courant — la pilule ne disparaît jamais.
        """
        saved = self._settings.value("pill/pos")
        if saved is not None:
            try:
                pt = QPoint(int(saved.x()), int(saved.y()))
            except (AttributeError, TypeError, ValueError):
                pt = None
            if pt is not None and self._is_visible_on_some_screen(pt):
                self.move(pt)
                return
        self.move(self._anchor_position())

    def _is_visible_on_some_screen(self, pt: QPoint) -> bool:
        """Vrai si un rectangle de la pilule à ``pt`` intersecte un écran."""
        rect = self.frameGeometry()
        rect.moveTopLeft(pt)
        for screen in QApplication.screens():
            if screen.availableGeometry().intersects(rect):
                return True
        return False

    def _save_position(self) -> None:
        self._settings.setValue("pill/pos", self.pos())

    def set_anchor(self, anchor: str) -> None:
        """Change le coin d'ancrage (réglage Phase 6) et y replace la pilule.

        On efface la position glissée mémorisée pour que le nouvel ancrage
        s'applique réellement (sinon le glisser précédent l'emporterait).
        """
        self._anchor = anchor
        self._settings.remove("pill/pos")
        self.move(self._anchor_position())

    def set_variant(self, variant: str) -> None:
        """Change la variante de pilule à chaud (réglage Phase 6).

        Chaque variante a sa propre géométrie : on redimensionne la fenêtre puis
        on la repositionne (la position glissée mémorisée est conservée si elle
        reste visible, sinon on retombe sur l'ancrage). Une variante inconnue
        retombe proprement sur D.
        """
        v = (variant or "D").strip().upper()
        self._variant = v if v in PILL_VARIANTS else "D"
        self._apply_variant_size()
        self._restore_position()
        self.update()

    # ------------------------------------------------------------------ #
    # Glisser-déposer pour repositionner
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if self._drag_offset is not None:
            self._drag_offset = None
            self._save_position()
            event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001
        """Menu clic-droit : paramètres (Phase 6) et « Quitter »."""
        menu = QMenu(self)
        settings_action = menu.addAction("Paramètres…")
        settings_action.setEnabled(self._on_settings is not None)
        menu.addSeparator()
        quit_action = menu.addAction("Quitter")
        chosen = menu.exec(event.globalPos())
        if chosen is settings_action and self._on_settings is not None:
            self._on_settings()
        elif chosen is quit_action and self._on_quit is not None:
            self._on_quit()

    # ------------------------------------------------------------------ #
    # Pilotage par l'état (appelable depuis n'importe quel thread)
    # ------------------------------------------------------------------ #
    def set_state(self, state: State) -> None:
        """Notifie une transition d'état (thread-safe via signal queued)."""
        self._state_signal.emit(state)

    def _apply_state(self, state: State) -> None:
        """Slot GUI : applique la transition reçue du moteur."""
        previous = self._state
        self._state = state
        if state is State.ECOUTE and previous is not State.ECOUTE:
            self._listen_started = time.monotonic()
            self._levels = deque([0.0] * _WAVE_BARS, maxlen=_WAVE_BARS)
        self.update()

    # ------------------------------------------------------------------ #
    # Animation (timer GUI)
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        if self._state is State.ECOUTE:
            level = min(1.0, max(0.0, self._level_provider() * _LEVEL_GAIN))
            self._levels.append(level)
            self._pulse_phase = (self._pulse_phase + 0.18) % (2 * math.pi)
            self.update()
        elif self._state in (State.TRANSCRIPTION, State.INJECTION):
            self._spinner_angle = (self._spinner_angle + 12.0) % 360.0
            self.update()
        # Au repos / erreur : rien à animer, on évite de repeindre inutilement.

    def _elapsed_text(self) -> str:
        secs = int(time.monotonic() - self._listen_started)
        return f"{secs // 60}:{secs % 60:02d}"

    # ------------------------------------------------------------------ #
    # Rendu (dispatch par variante ; seule D est réalisée)
    # ------------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:  # noqa: ANN001
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        PILL_VARIANTS[self._variant](self, painter)
        painter.end()

    def _paint_variant_d(self, p: QPainter) -> None:
        """Variante D — pilule complète (par défaut)."""
        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        radius = rect.height() / 2

        if self._state is State.ECOUTE:
            self._paint_listening(p, rect, radius)
        elif self._state in (State.TRANSCRIPTION, State.INJECTION):
            self._paint_processing(p, rect, radius)
        elif self._state is State.ERREUR:
            self._paint_filled(p, rect, radius, _ORANGE, "erreur", icon="warn")
        else:  # REPOS
            self._paint_idle(p, rect, radius)

    # ================================================================== #
    # Variante A — barre minimale (point coloré + libellé)
    #
    # Même traitement colorimétrique que D (validé en HDR) : repos « fantôme »
    # quasi transparent, états actifs en aplat plein saturé + contenu blanc.
    # ================================================================== #
    def _paint_variant_a(self, p: QPainter) -> None:
        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        radius = rect.height() / 2
        cy = rect.center().y()
        x = rect.left() + 16
        if self._state is State.ECOUTE:
            self._fill_pill(p, rect, radius, _RED)
            self._draw_state_dot(p, x, cy, _WHITE, pulsing=True)
            self._draw_label(p, rect, x + 16, "écoute…", _WHITE)
        elif self._state in (State.TRANSCRIPTION, State.INJECTION):
            self._fill_pill(p, rect, radius, _BLUE)
            self._draw_state_dot(p, x, cy, _WHITE)
            self._draw_label(p, rect, x + 16, "traitement…", _WHITE)
        elif self._state is State.ERREUR:
            self._fill_pill(p, rect, radius, _ORANGE)
            self._draw_state_dot(p, x, cy, _WHITE)
            self._draw_label(p, rect, x + 16, "erreur", _WHITE)
        else:  # REPOS
            self._ghost_pill(p, rect, radius)
            self._draw_state_dot(p, x, cy, _GREY)
            self._draw_label(p, rect, x + 16, "prêt", _GREY)

    # ================================================================== #
    # Variante B — forme d'onde (micro + barres + spinner)
    # ================================================================== #
    def _paint_variant_b(self, p: QPainter) -> None:
        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        radius = rect.height() / 2
        cy = rect.center().y()
        x = rect.left() + 16
        wf_x = x + 22
        if self._state is State.ECOUTE:
            self._fill_pill(p, rect, radius, _RED)
            self._draw_mic(p, x, cy, _WHITE, size=14)
            self._draw_waveform(p, wf_x, cy, rect.right() - 14 - wf_x)
        elif self._state in (State.TRANSCRIPTION, State.INJECTION):
            self._fill_pill(p, rect, radius, _BLUE)
            self._draw_spinner(p, x + 6, cy, 7.0)
            self._draw_label(p, rect, x + 24, "traitement…", _WHITE)
        elif self._state is State.ERREUR:
            self._fill_pill(p, rect, radius, _ORANGE)
            self._draw_warning(p, x, cy, _WHITE)
            self._draw_label(p, rect, x + 22, "erreur", _WHITE)
        else:  # REPOS — vague plate = silence
            self._ghost_pill(p, rect, radius)
            self._draw_mic(p, x, cy, _GREY, size=14)
            self._draw_waveform(p, wf_x, cy, rect.right() - 14 - wf_x, _GREY, flat=True)

    # ================================================================== #
    # Variante C — badge circulaire ~50×50 (anneau + halo / spinner)
    # ================================================================== #
    def _paint_variant_c(self, p: QPainter) -> None:
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        ring_r = min(self.width(), self.height()) / 2.0 - 4.0
        rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)

        def disc(bg: QColor) -> None:
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawEllipse(rect)

        if self._state is State.ECOUTE:
            # Halo pulsé autour du badge rouge plein.
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase)
            halo = QColor(_RED)
            halo.setAlphaF(0.25 + 0.25 * pulse)
            p.setPen(QPen(halo, 3.0))
            p.setBrush(Qt.NoBrush)
            hr = ring_r + 3.0 + 2.0 * pulse
            p.drawEllipse(QPointF(cx, cy), hr, hr)
            disc(_RED)
            self._draw_mic(p, cx - 18 * 0.21, cy, _WHITE, size=18)
        elif self._state in (State.TRANSCRIPTION, State.INJECTION):
            # Badge bleu plein + anneau blanc tournant (épouse le bord).
            disc(_BLUE)
            self._draw_spinner(p, cx, cy, ring_r - 1.5, _WHITE, width=2.5)
        elif self._state is State.ERREUR:
            disc(_ORANGE)
            self._draw_warning(p, cx - 7, cy, _WHITE)
        else:  # REPOS — badge fantôme, anneau pointillé gris
            disc(_GHOST_BG)
            p.setPen(QPen(_GREY, 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(rect)
            self._draw_mic(p, cx - 18 * 0.21, cy, _GREY, size=18)

    # --- helpers partagés des variantes --------------------------------- #
    def _fill_pill(
        self, p: QPainter, rect: QRectF, radius: float, color: QColor
    ) -> None:
        """Aplat plein saturé (état actif) — sûr en HDR, contenu blanc par-dessus."""
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, color)

    def _ghost_pill(self, p: QPainter, rect: QRectF, radius: float) -> None:
        """Capsule « fantôme » du repos : fond quasi transparent + contour pointillé."""
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, _GHOST_BG)
        p.setPen(QPen(_GREY, 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _draw_state_dot(
        self, p: QPainter, x: float, cy: float, color: QColor, *, pulsing: bool = False
    ) -> None:
        r = 4.5
        if pulsing:
            pulse = 0.5 + 0.5 * math.sin(self._pulse_phase)
            halo = QColor(color)
            halo.setAlphaF(0.22 + 0.28 * pulse)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            hr = r + 2.0 + 2.0 * pulse
            p.drawEllipse(QPointF(x, cy), hr, hr)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(x, cy), r, r)

    def _draw_label(
        self, p: QPainter, rect: QRectF, x: float, text: str, color: QColor
    ) -> None:
        p.setPen(color)
        p.setFont(self._font)
        p.drawText(
            QRectF(x, rect.top(), rect.right() - x, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            text,
        )

    # --- états ---------------------------------------------------------- #
    def _paint_idle(self, p: QPainter, rect: QRectF, radius: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        # Fond quasi transparent + contour pointillé discret.
        p.fillPath(path, QColor(248, 247, 244, 36))
        pen = QPen(_GREY, 1.5, Qt.DashLine)
        p.setPen(pen)
        p.drawPath(path)
        x = rect.left() + 16
        self._draw_mic(p, x, rect.center().y(), _GREY, size=13)
        p.setPen(_GREY)
        p.setFont(self._font)
        p.drawText(
            QRectF(x + 20, rect.top(), rect.width() - 36, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            "Voix→Clavier",
        )

    def _paint_listening(self, p: QPainter, rect: QRectF, radius: float) -> None:
        # Pulsation : halo coloré dont l'opacité respire.
        pulse = 0.5 + 0.5 * math.sin(self._pulse_phase)
        halo = QColor(_RED)
        halo.setAlphaF(0.25 + 0.20 * pulse)
        halo_pen = QPen(halo, 3.0)
        p.setPen(halo_pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, _RED)

        x = rect.left() + 16
        cy = rect.center().y()
        self._draw_mic(p, x, cy, _WHITE, size=13)
        x += 22
        # Timer.
        p.setPen(_WHITE)
        f = QFont(self._font)
        f.setBold(True)
        p.setFont(f)
        timer_text = self._elapsed_text()
        fm = QFontMetrics(f)
        tw = fm.horizontalAdvance(timer_text)
        p.drawText(
            QRectF(x, rect.top(), tw + 6, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            timer_text,
        )
        x += tw + 12
        # Waveform : barres réagissant au volume réel du micro.
        self._draw_waveform(p, x, cy, rect.right() - 14 - x)

    def _paint_processing(self, p: QPainter, rect: QRectF, radius: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, _BLUE)
        x = rect.left() + 18
        cy = rect.center().y()
        self._draw_spinner(p, x, cy, 8.0)
        p.setPen(_WHITE)
        p.setFont(self._font)
        p.drawText(
            QRectF(x + 18, rect.top(), rect.width() - 40, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            "traitement…",
        )

    def _paint_filled(
        self, p: QPainter, rect: QRectF, radius: float,
        color: QColor, text: str, *, icon: str = "mic",
    ) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, color)
        x = rect.left() + 16
        cy = rect.center().y()
        if icon == "warn":
            self._draw_warning(p, x, cy, _WHITE)
        else:
            self._draw_mic(p, x, cy, _WHITE, size=13)
        p.setPen(_WHITE)
        p.setFont(self._font)
        p.drawText(
            QRectF(x + 20, rect.top(), rect.width() - 36, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            text,
        )

    # --- primitives de dessin ------------------------------------------- #
    def _draw_mic(self, p: QPainter, x: float, cy: float, color: QColor, *, size: int) -> None:
        pen = QPen(color, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        s = size
        # Capsule.
        p.drawRoundedRect(QRectF(x, cy - s / 2, s * 0.42, s * 0.62), s * 0.21, s * 0.21)
        cx = x + s * 0.21
        # Arceau.
        p.drawArc(QRectF(x - s * 0.07, cy - s * 0.12, s * 0.56, s * 0.5), 200 * 16, 140 * 16)
        # Pied + base.
        p.drawLine(QPoint(int(cx), int(cy + s * 0.38)), QPoint(int(cx), int(cy + s * 0.55)))
        p.drawLine(
            QPoint(int(cx - s * 0.22), int(cy + s * 0.55)),
            QPoint(int(cx + s * 0.22), int(cy + s * 0.55)),
        )

    def _draw_waveform(
        self, p: QPainter, x: float, cy: float, width: float,
        color: QColor | None = None, *, flat: bool = False,
    ) -> None:
        if width <= 0:
            return
        bars = list(self._levels)
        n = len(bars)
        gap = 2.0
        bar_w = max(2.0, (width - gap * (n - 1)) / n)
        max_h = self.height() * 0.5
        p.setPen(Qt.NoPen)
        p.setBrush(color or QColor(255, 255, 255, 200))
        bx = x
        for lvl in bars:
            h = 3.0 if flat else max(3.0, lvl * max_h)
            p.drawRoundedRect(QRectF(bx, cy - h / 2, bar_w, h), 1.5, 1.5)
            bx += bar_w + gap

    def _draw_spinner(
        self, p: QPainter, x: float, cy: float, radius: float,
        color: QColor = _WHITE, *, width: float = 2.2,
    ) -> None:
        rect = QRectF(x - radius, cy - radius, radius * 2, radius * 2)
        # Anneau de fond ténu.
        faint = QColor(color)
        faint.setAlpha(70)
        p.setPen(QPen(faint, width))
        p.drawArc(rect, 0, 360 * 16)
        # Arc mobile.
        strong = QColor(color)
        strong.setAlpha(235)
        p.setPen(QPen(strong, width, Qt.SolidLine, Qt.RoundCap))
        start = int(-self._spinner_angle * 16)
        p.drawArc(rect, start, 100 * 16)

    def _draw_warning(self, p: QPainter, x: float, cy: float, color: QColor) -> None:
        pen = QPen(color, 1.6)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        s = 14
        path = QPainterPath()
        path.moveTo(x + s / 2, cy - s / 2)
        path.lineTo(x + s, cy + s / 2)
        path.lineTo(x, cy + s / 2)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPoint(int(x + s / 2), int(cy - s * 0.18)),
                   QPoint(int(x + s / 2), int(cy + s * 0.18)))

    # ------------------------------------------------------------------ #
    # Cycle de vie
    # ------------------------------------------------------------------ #
    def shutdown(self) -> None:
        """Arrête l'animation et ferme la fenêtre (sauvegarde la position)."""
        self._timer.stop()
        self._save_position()
        self.close()


# Dispatch de rendu par variante (Section 1 des wireframes). Les quatre variantes
# sont réalisées ; chacune a sa peinture dédiée et sa géométrie (_VARIANT_SIZES).
PILL_VARIANTS: dict[str, Callable[["FloatingPill", QPainter], None]] = {
    "A": FloatingPill._paint_variant_a,
    "B": FloatingPill._paint_variant_b,
    "C": FloatingPill._paint_variant_c,
    "D": FloatingPill._paint_variant_d,
}


def make_pill(
    variant: str,
    *,
    anchor: str = "bas-droite",
    level_provider: Callable[[], float] | None = None,
    on_quit: Callable[[], None] | None = None,
    on_settings: Callable[[], None] | None = None,
) -> FloatingPill:
    """Fabrique la pilule pour la variante demandée (repli sur D si inconnue)."""
    return FloatingPill(
        variant=variant,
        anchor=anchor,
        level_provider=level_provider,
        on_quit=on_quit,
        on_settings=on_settings,
    )
