"""Pilule flottante always-on-top — Phase 5 (Section 1 des wireframes).

Petite fenêtre sans bordure, toujours au-dessus des autres applications, à fond
translucide, repositionnable par glisser et persistante en position. Elle reflète
en temps réel la machine à états de la dictée (Phase 2) :

- **repos**          : pilule quasi-invisible (contour pointillé, mic gris) ;
- **écoute**         : fond rouge plein + timer d'enregistrement + waveform
  réagissant au volume réel du micro (:pyattr:`Recorder.level`) ;
- **transcription**  : fond bleu plein + spinner rotatif + « traitement… » ;
- **erreur**         : fond orange bref avant retour au repos.

Architecture interchangeable (décision arrêtée de la roadmap) : seule la
**variante D (pilule complète)** est implémentée, mais le rendu est dispatché par
identifiant de variante (:data:`PILL_VARIANTS`) et la fabrique :func:`make_pill`
lit ``config.variante_pilule``. Les variantes A, B et C sont différées : leur
point d'extension (une méthode de peinture dédiée) est prévu mais non réalisé.

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

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer, Signal
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

# --- Palette (reprise de la Section 1 des wireframes, variante D) ----------- #
_RED = QColor("#ee0033")        # écoute
_BLUE = QColor("#2266ee")       # transcription
_ORANGE = QColor("#ee8800")     # erreur
_GREY = QColor("#bbbbbb")       # repos (contour + texte)
_WHITE = QColor("#ffffff")

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
    ) -> None:
        super().__init__(None)
        self._on_quit = on_quit
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
        self.setFixedSize(210, 48)
        self.setWindowTitle("Voix → Clavier")
        # Police « manuscrite » des wireframes si disponible, sinon repli système.
        self._font = QFont("Segoe UI", 11, QFont.DemiBold)

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
        """Menu clic-droit : permet de quitter même sans icône systray."""
        menu = QMenu(self)
        quit_action = menu.addAction("Quitter")
        chosen = menu.exec(event.globalPos())
        if chosen is quit_action and self._on_quit is not None:
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

    def _draw_waveform(self, p: QPainter, x: float, cy: float, width: float) -> None:
        if width <= 0:
            return
        bars = list(self._levels)
        n = len(bars)
        gap = 2.0
        bar_w = max(2.0, (width - gap * (n - 1)) / n)
        max_h = self.height() * 0.5
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 200))
        bx = x
        for lvl in bars:
            h = max(3.0, lvl * max_h)
            p.drawRoundedRect(QRectF(bx, cy - h / 2, bar_w, h), 1.5, 1.5)
            bx += bar_w + gap

    def _draw_spinner(self, p: QPainter, x: float, cy: float, radius: float) -> None:
        rect = QRectF(x - radius, cy - radius, radius * 2, radius * 2)
        # Anneau de fond ténu.
        p.setPen(QPen(QColor(255, 255, 255, 70), 2.2))
        p.drawArc(rect, 0, 360 * 16)
        # Arc mobile.
        p.setPen(QPen(QColor(255, 255, 255, 235), 2.2, Qt.SolidLine, Qt.RoundCap))
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


# Dispatch de rendu par variante. Décision arrêtée : seule D est réalisée ;
# A, B et C sont différées mais le point d'extension (une fonction de peinture
# par variante) est prévu — il suffira d'ajouter l'entrée correspondante.
PILL_VARIANTS: dict[str, Callable[["FloatingPill", QPainter], None]] = {
    "D": FloatingPill._paint_variant_d,
    # "A": FloatingPill._paint_variant_a,  # différée
    # "B": FloatingPill._paint_variant_b,  # différée
    # "C": FloatingPill._paint_variant_c,  # différée
}


def make_pill(
    variant: str,
    *,
    anchor: str = "bas-droite",
    level_provider: Callable[[], float] | None = None,
    on_quit: Callable[[], None] | None = None,
) -> FloatingPill:
    """Fabrique la pilule pour la variante demandée (repli sur D si inconnue)."""
    return FloatingPill(
        variant=variant,
        anchor=anchor,
        level_provider=level_provider,
        on_quit=on_quit,
    )
