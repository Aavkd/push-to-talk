"""Fenêtre de paramètres — Phase 6 (Section 2 des wireframes, variante A).

Décision arrêtée de la roadmap : **carte compacte (variante A)** comme interface
principale. C'est une petite fenêtre flottante sans bordure native, dotée d'une
**barre de titre sombre** (« ⚙ Paramètres » + ✕), repositionnable par glisser,
exposant **tous les réglages** de l'application :

- **Déclenchement** : mode (push-to-talk / toggle), raccourci (capture clavier).
- **Transcription** : modèle, device (CUDA / CPU), ``compute_type``, langue.
- **Micro** : périphérique d'entrée, délai de restauration du presse-papiers.
- **Interface** : variante de pilule (A/B/C/D), position.
- **Démarrage** : lancer au démarrage de Windows, afficher l'icône systray.

La fenêtre ne touche **jamais** directement à la configuration de l'application :
au clic sur « Enregistrer », elle construit un nouvel objet :class:`Config` et le
remet à un callback ``on_apply`` (fourni par :mod:`voix_clavier.app`). C'est lui
qui décide ce qui s'applique à chaud et ce qui exige un redémarrage (chargement du
modèle). ``on_apply`` renvoie la liste des champs nécessitant un redémarrage ; la
fenêtre affiche alors une bannière le signalant (roadmap : « indiquer qu'un
redémarrage est requis »).

La validation des entrées vit ici : le raccourci capturé doit être une combinaison
non vide et analysable par :func:`voix_clavier.hotkey.parse_combo` ; les autres
réglages sont contraints par des listes déroulantes, donc intrinsèquement valides.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Callable, Sequence

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..hotkey import parse_combo

# Callback de prise en compte : reçoit la nouvelle config, renvoie la liste des
# champs exigeant un redémarrage (vide si tout s'applique à chaud).
ApplyCallback = Callable[[Config], "Sequence[str]"]

# Listes déroulantes (valeur de config -> libellé affiché). L'ordre fixe l'ordre
# d'affichage. La valeur stockée reste la clé technique attendue par le moteur.
_MODES: list[tuple[str, str]] = [
    ("push-to-talk", "Push-to-talk (maintenir)"),
    ("toggle", "Toggle (appui / appui)"),
]
# Modèles : large-v3 par défaut + options plus légères (repli CPU possible).
_MODELS: list[tuple[str, str]] = [
    ("large-v3", "large-v3 (qualité max)"),
    ("medium", "medium"),
    ("small", "small"),
    ("base", "base"),
    ("tiny", "tiny (léger)"),
]
_DEVICES: list[tuple[str, str]] = [
    ("cuda", "CUDA (GPU)"),
    ("cpu", "CPU"),
]
_COMPUTE_TYPES: list[tuple[str, str]] = [
    ("float16", "float16 (GPU)"),
    ("int8_float16", "int8_float16"),
    ("int8", "int8 (CPU)"),
]
# La langue "auto" est stockée "" dans la config ; on la traduit ici.
_LANGUAGES: list[tuple[str, str]] = [
    ("fr", "Français"),
    ("en", "Anglais"),
    ("auto", "Auto-détection"),
]
_VARIANTS: list[tuple[str, str]] = [
    ("D", "Variante D — pilule complète"),
    ("A", "Variante A — barre minimale (différée)"),
    ("B", "Variante B — forme d'onde (différée)"),
    ("C", "Variante C — badge circulaire (différée)"),
]
_POSITIONS: list[tuple[str, str]] = [
    ("bas-droite", "Bas-droite"),
    ("bas-gauche", "Bas-gauche"),
    ("haut-droite", "Haut-droite"),
    ("haut-gauche", "Haut-gauche"),
    ("centre", "Centre"),
]

# Affichage « joli » d'un jeton de raccourci pour la capture clavier.
_TOKEN_DISPLAY: dict[str, str] = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Maj",
    "win": "Win",
    "space": "Espace",
    "enter": "Entrée",
    "tab": "Tab",
    "esc": "Échap",
}

# Modificateurs Qt -> jeton canonique (ordre d'affichage stable). On manipule les
# valeurs entières : selon la version de PySide6, les énums ne se comparent pas
# toujours directement aux ``int`` renvoyés par ``event.key()``/``modifiers()``.
_QT_MODIFIERS: list[tuple[int, str]] = [
    (int(Qt.ControlModifier.value), "ctrl"),
    (int(Qt.AltModifier.value), "alt"),
    (int(Qt.ShiftModifier.value), "shift"),
    (int(Qt.MetaModifier.value), "win"),
]

# Touches Qt « nommées » -> jeton canonique (cohérent avec hotkey._ALIAS).
_QT_NAMED_KEYS: dict[int, str] = {
    int(Qt.Key_Space.value): "space",
    int(Qt.Key_Return.value): "enter",
    int(Qt.Key_Enter.value): "enter",
    int(Qt.Key_Tab.value): "tab",
}

# Touches Qt purement modificatrices : on les ignore comme « touche principale ».
_QT_MODIFIER_KEYS = frozenset(
    int(k.value)
    for k in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr)
)

_KEY_F1 = int(Qt.Key_F1.value)
_KEY_F35 = int(Qt.Key_F35.value)
_KEY_ESCAPE = int(Qt.Key_Escape.value)


def _pretty_combo(combo: str) -> str:
    """``"ctrl+space"`` -> ``"Ctrl+Espace"`` pour l'affichage."""
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    return "+".join(_TOKEN_DISPLAY.get(p, p.upper() if len(p) == 1 else p.capitalize())
                     for p in parts)


def _qt_event_to_combo(event: QKeyEvent) -> str | None:
    """Traduit un évènement clavier Qt en chaîne de raccourci canonique.

    Renvoie ``None`` tant que seule une touche modificatrice est pressée (on
    attend une touche « principale »). Format de sortie : ``"ctrl+space"``,
    compatible avec :func:`voix_clavier.hotkey.parse_combo`.
    """
    key = int(event.key())
    if key in _QT_MODIFIER_KEYS:
        return None  # modificateur seul : on attend la touche principale

    mods = int(event.modifiers().value)
    tokens = [tok for flag, tok in _QT_MODIFIERS if mods & flag]

    main = _QT_NAMED_KEYS.get(key)
    if main is None:
        if _KEY_F1 <= key <= _KEY_F35:
            main = f"f{key - _KEY_F1 + 1}"
        else:
            text = event.text().strip().lower()
            if text and text.isalnum():
                main = text
            else:
                return None  # touche non gérée (ponctuation morte, etc.)

    tokens.append(main)
    return "+".join(tokens)


class _HotkeyButton(QPushButton):
    """Bouton de capture de raccourci : clic → presser une combinaison.

    Stocke la combinaison au format canonique (``self.combo``) et affiche une
    version lisible. ``Échap`` annule la capture et restaure la valeur courante.
    """

    def __init__(self, combo: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.combo = combo
        self._capturing = False
        self.setCheckable(True)
        self.clicked.connect(self._toggle_capture)
        self._refresh_label()

    def _refresh_label(self) -> None:
        if self._capturing:
            self.setText("Pressez une combinaison…")
        else:
            self.setText(_pretty_combo(self.combo) or "—")

    def _toggle_capture(self) -> None:
        if self.isChecked():
            self._capturing = True
            self.grabKeyboard()
        else:
            self._capturing = False
            self.releaseKeyboard()
        self._refresh_label()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if int(event.key()) == _KEY_ESCAPE:
            self._end_capture()
            return
        combo = _qt_event_to_combo(event)
        if combo is not None:
            self.combo = combo
            self._end_capture()
        # Sinon (modificateur seul) : on continue d'attendre la touche principale.
        event.accept()

    def _end_capture(self) -> None:
        self._capturing = False
        self.setChecked(False)
        self.releaseKeyboard()
        self._refresh_label()


def _fit_popup(box: QComboBox) -> None:
    """Élargit la liste déroulante pour afficher les libellés en entier.

    Le contrôle fermé reste compact (largeur bornée par la ligne), mais la liste
    ouverte doit montrer les noms longs (périphériques micro) sans troncature.
    """
    fm = box.fontMetrics()
    widest = max(
        (fm.horizontalAdvance(box.itemText(i)) for i in range(box.count())),
        default=0,
    )
    box.view().setMinimumWidth(widest + 36)


def _make_combo(items: list[tuple[str, str]], current: str) -> QComboBox:
    """Construit une liste déroulante (valeur, libellé) positionnée sur ``current``."""
    box = QComboBox()
    for value, label in items:
        box.addItem(label, value)
    idx = box.findData(current)
    if idx < 0:
        # Valeur de config hors liste (ex. modèle exotique) : on l'ajoute en tête.
        box.insertItem(0, current, current)
        idx = 0
    box.setCurrentIndex(idx)
    _fit_popup(box)
    return box


class SettingsWindow(QWidget):
    """Carte compacte de paramètres (variante A), flottante et sans bordure native."""

    def __init__(
        self,
        config: Config,
        *,
        on_apply: ApplyCallback,
        devices: list[tuple[int, str]] | None = None,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(None)
        # Copie défensive : on n'altère pas la config de l'app tant qu'on n'a pas
        # cliqué « Enregistrer ».
        self._config = copy.deepcopy(config)
        self._on_apply = on_apply
        self._on_closed = on_closed
        self._devices = devices or []
        self._drag_offset: QPoint | None = None

        self._init_window()
        self._build_ui()
        self._center_on_screen()

    # ------------------------------------------------------------------ #
    # Fenêtre
    # ------------------------------------------------------------------ #
    def _init_window(self) -> None:
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setWindowTitle("Voix → Clavier — Paramètres")
        self.setFixedWidth(340)
        self.setStyleSheet(
            """
            QWidget#card { background: #faf8f5; }
            QLabel#title { color: #fff; font-weight: 700; font-size: 14px; }
            QLabel#close { color: #aaa; font-size: 16px; font-weight: 700; }
            QLabel#close:hover { color: #fff; }
            QLabel#section {
                color: #888; font-size: 11px; font-weight: 700;
                text-transform: uppercase; letter-spacing: 1px;
            }
            QLabel#field { color: #444; font-size: 13px; }
            QLabel#banner { color: #aa5500; font-size: 12px; }
            QFrame#row { border: none; border-bottom: 1px dashed #e8e4de; }
            QComboBox, QSpinBox {
                background: #f4f1ed; border: 1px solid #d5d0c8;
                border-radius: 4px; padding: 3px 6px; color: #333; font-size: 12px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            /* Liste déroulante : sans ce bloc, le texte des items hérite d'une
               couleur quasi blanche illisible sur fond clair. */
            QComboBox QAbstractItemView {
                background: #ffffff; color: #333333; outline: 0;
                border: 1px solid #d5d0c8;
                selection-background-color: #2266ee; selection-color: #ffffff;
            }
            QComboBox QAbstractItemView::item {
                min-height: 22px; padding: 3px 8px; color: #333333;
            }
            QPushButton#hotkey {
                background: #f4f1ed; border: 1px solid #d5d0c8;
                border-radius: 4px; padding: 3px 8px; color: #333; font-size: 12px;
            }
            QPushButton#hotkey:checked { border-color: #2266ee; color: #2266ee; }
            QPushButton#cancel {
                background: #faf8f5; border: 1px solid #d5d0c8;
                border-radius: 4px; padding: 5px 14px; color: #888; font-size: 13px;
            }
            QPushButton#save {
                background: #222; border: none; border-radius: 4px;
                padding: 5px 14px; color: #fff; font-size: 13px; font-weight: 600;
            }
            QPushButton#save:hover { background: #000; }
            """
        )

    def _center_on_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.adjustSize()
        x = geo.left() + (geo.width() - self.width()) // 2
        y = geo.top() + (geo.height() - self.height()) // 2
        self.move(x, y)

    # ------------------------------------------------------------------ #
    # Construction de l'interface
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        card = QWidget(self)
        card.setObjectName("card")
        outer.addWidget(card)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        card_layout.addWidget(self._build_title_bar())

        body = QVBoxLayout()
        body.setContentsMargins(14, 10, 14, 12)
        body.setSpacing(4)

        cfg = self._config

        # --- Déclenchement ---
        body.addWidget(self._section("Déclenchement"))
        self._mode = _make_combo(_MODES, cfg.mode)
        body.addWidget(self._row("Mode", self._mode))
        self._hotkey = _HotkeyButton(cfg.raccourci)
        self._hotkey.setObjectName("hotkey")
        body.addWidget(self._row("Raccourci", self._hotkey))

        # --- Transcription / Modèle ---
        body.addWidget(self._section("Transcription"))
        self._model = _make_combo(_MODELS, cfg.modele)
        body.addWidget(self._row("Modèle", self._model))
        self._device = _make_combo(_DEVICES, cfg.device)
        body.addWidget(self._row("Device", self._device))
        self._compute = _make_combo(_COMPUTE_TYPES, cfg.compute_type)
        body.addWidget(self._row("Calcul", self._compute))
        lang = "auto" if cfg.langue_whisper is None else cfg.langue
        self._language = _make_combo(_LANGUAGES, lang)
        body.addWidget(self._row("Langue", self._language))

        # --- Micro ---
        body.addWidget(self._section("Micro"))
        self._mic = self._build_mic_combo(cfg.peripherique)
        body.addWidget(self._row("Périphérique", self._mic))
        self._delay = QSpinBox()
        self._delay.setRange(0, 1000)
        self._delay.setSingleStep(10)
        self._delay.setSuffix(" ms")
        self._delay.setValue(int(cfg.delai_restauration_ms))
        body.addWidget(self._row("Délai presse-papiers", self._delay))

        # --- Interface ---
        body.addWidget(self._section("Interface"))
        self._variant = _make_combo(_VARIANTS, cfg.variante_pilule)
        body.addWidget(self._row("Pilule", self._variant))
        self._position = _make_combo(_POSITIONS, cfg.position_pilule)
        body.addWidget(self._row("Position", self._position))

        # --- Démarrage ---
        body.addWidget(self._section("Démarrage"))
        self._autostart = QCheckBox("Lancer au démarrage de Windows")
        self._autostart.setChecked(bool(cfg.lancer_au_demarrage))
        body.addWidget(self._autostart)
        self._systray = QCheckBox("Afficher l'icône systray")
        self._systray.setChecked(bool(cfg.afficher_systray))
        body.addWidget(self._systray)

        # Bannière de validation / redémarrage (cachée au départ).
        self._banner = QLabel("")
        self._banner.setObjectName("banner")
        self._banner.setWordWrap(True)
        self._banner.hide()
        body.addWidget(self._banner)

        body.addLayout(self._build_footer())

        # Zone défilante au cas où l'écran est petit ; sinon la carte garde sa
        # taille naturelle compacte.
        content = QWidget()
        content.setObjectName("card")
        content.setLayout(body)
        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(560)
        card_layout.addWidget(scroll)

    def _build_title_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(32)
        bar.setStyleSheet("background: #222;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 10, 0)
        title = QLabel("⚙ Paramètres")
        title.setObjectName("title")
        close = QLabel("✕")
        close.setObjectName("close")
        close.setCursor(Qt.PointingHandCursor)
        close.mousePressEvent = lambda _e: self.close()  # type: ignore[assignment]
        layout.addWidget(title)
        layout.addStretch(1)
        layout.addWidget(close)
        # La barre de titre sert aussi de poignée de déplacement.
        bar.mousePressEvent = self._bar_mouse_press  # type: ignore[assignment]
        bar.mouseMoveEvent = self._bar_mouse_move    # type: ignore[assignment]
        bar.mouseReleaseEvent = self._bar_mouse_release  # type: ignore[assignment]
        return bar

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 10, 0, 0)
        footer.addStretch(1)
        cancel = QPushButton("Annuler")
        cancel.setObjectName("cancel")
        cancel.clicked.connect(self.close)
        save = QPushButton("Enregistrer")
        save.setObjectName("save")
        save.clicked.connect(self._on_save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        return footer

    def _build_mic_combo(self, current: str) -> QComboBox:
        box = QComboBox()
        box.addItem("Périphérique par défaut", "")
        for idx, name in self._devices:
            box.addItem(name, str(idx))
        # La config peut stocker "" (défaut), un index, ou un nom. On positionne au
        # mieux : index exact, sinon nom, sinon « défaut ».
        cur = (current or "").strip()
        pos = box.findData(cur) if cur else 0
        if pos < 0:
            pos = box.findText(cur)
        box.setCurrentIndex(pos if pos >= 0 else 0)
        _fit_popup(box)
        return box

    # --- fabriques de lignes -------------------------------------------- #
    def _section(self, title: str) -> QLabel:
        label = QLabel(title)
        label.setObjectName("section")
        label.setContentsMargins(0, 8, 0, 2)
        return label

    def _row(self, label: str, control: QWidget) -> QFrame:
        """Ligne label↔contrôle avec séparateur pointillé (cf. wireframe « SettingRow »)."""
        row = QFrame()
        row.setObjectName("row")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 7, 0, 7)
        layout.setSpacing(10)
        text = QLabel(label)
        text.setObjectName("field")
        layout.addWidget(text)
        layout.addStretch(1)
        # Contrôle compact, aligné à droite comme dans la maquette.
        control.setMinimumWidth(150)
        control.setMaximumWidth(190)
        layout.addWidget(control)
        return row

    # ------------------------------------------------------------------ #
    # Déplacement par la barre de titre
    # ------------------------------------------------------------------ #
    def _bar_mouse_press(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def _bar_mouse_move(self, event) -> None:  # noqa: ANN001
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def _bar_mouse_release(self, event) -> None:  # noqa: ANN001
        self._drag_offset = None
        event.accept()

    # ------------------------------------------------------------------ #
    # Collecte + validation + application
    # ------------------------------------------------------------------ #
    def _collect(self) -> Config:
        """Construit un nouvel objet Config à partir des widgets."""
        lang_value = self._language.currentData()
        langue = "" if lang_value == "auto" else lang_value
        return replace(
            self._config,
            mode=self._mode.currentData(),
            raccourci=self._hotkey.combo,
            modele=self._model.currentData(),
            device=self._device.currentData(),
            compute_type=self._compute.currentData(),
            langue=langue,
            peripherique=self._mic.currentData(),
            delai_restauration_ms=int(self._delay.value()),
            variante_pilule=self._variant.currentData(),
            position_pilule=self._position.currentData(),
            lancer_au_demarrage=self._autostart.isChecked(),
            afficher_systray=self._systray.isChecked(),
        )

    def _show_banner(self, message: str, *, error: bool = False) -> None:
        self._banner.setStyleSheet("color: #cc0000;" if error else "color: #aa5500;")
        self._banner.setText(message)
        self._banner.show()

    def _on_save(self) -> None:
        # Validation du raccourci : non vide et analysable.
        combo = (self._hotkey.combo or "").strip()
        try:
            if not parse_combo(combo):
                raise ValueError("vide")
        except ValueError:
            self._show_banner(
                "Raccourci invalide — cliquez puis pressez une combinaison.",
                error=True,
            )
            return

        new_config = self._collect()
        try:
            restart_fields = list(self._on_apply(new_config))
        except Exception as exc:  # noqa: BLE001 - l'échec d'application ne ferme pas la fenêtre
            self._show_banner(f"Échec de l'enregistrement : {exc}", error=True)
            return

        if restart_fields:
            # On a tout enregistré ; certains réglages (modèle / device / calcul)
            # n'agissent qu'au prochain lancement.
            noms = ", ".join(restart_fields)
            self._show_banner(
                f"Enregistré. Redémarrage requis pour : {noms}."
            )
            return

        self.close()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._hotkey._capturing:  # libère le clavier si capture en cours
            self._hotkey._end_capture()
        if self._on_closed is not None:
            self._on_closed()
        super().closeEvent(event)
