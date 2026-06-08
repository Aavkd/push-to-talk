# Roadmap de réalisation — Outil de dictée vocale "Voix → Clavier" (Windows)

Document de pilotage pour Claude Code. À lire avec la spécification technique (`voix-vers-clavier-spec.md`) et les wireframes (`Wireframes.html` + `design-canvas.jsx`).

La roadmap est découpée en **9 phases incrémentales**. Chaque phase produit un livrable testable avant de passer à la suivante. Le cœur risqué (audio → texte → injection) est validé en premier ; l'UI vient ensuite.

---

## Principe directeur

Construire de l'intérieur vers l'extérieur : moteur fonctionnel d'abord, puis confort (modes), puis robustesse, puis UI (systray → pilule → fenêtre de paramètres), puis packaging. Aucune phase UI ne démarre tant que le moteur sous-jacent n'est pas validé.

À chaque phase, l'application doit rester lançable et utilisable, même de façon partielle.

---

## Phase 0 — Préparation de l'environnement

**Objectif :** un projet propre, le GPU exploité, le modèle Whisper chargeable.

**Tâches :**
- Initialiser le projet Python (gestionnaire d'environnement, `pyproject.toml` ou `requirements.txt`).
- Installer et vérifier la chaîne CUDA pour `faster-whisper` sur la RTX 4090 mobile (drivers NVIDIA, cuDNN/CUDA runtime requis par CTranslate2).
- Charger `large-v3` en `device="cuda", compute_type="float16"` et transcrire un fichier audio de test français contenant des accents → vérifier la sortie.
- Définir l'arborescence du projet (modules séparés : audio, transcription, injection, hotkey, état, config, UI).
- Choisir le format de configuration : **`config.toml`** (décision arrêtée) et écrire un schéma par défaut avec commentaires.

**Critère de validation :** un script en ligne de commande transcrit un `.wav` français correctement, sur GPU, en moins de ~2 s pour quelques phrases.

**Dépendances :** aucune.

---

## Phase 1 — Cœur audio → texte → injection (sans UI)

**Objectif :** valider la boucle complète sans interface. C'est la partie risquée.

**Composants design concernés :** flux presse-papiers (Section 3), machine à états (squelette).

**Tâches :**
- **Capture micro** avec `sounddevice` : enregistrer dans un buffer numpy au format attendu par Whisper (16 kHz mono).
- **Transcription** : passer le buffer à l'instance `faster-whisper` chargée une seule fois au démarrage (jamais rechargée par dictée).
- **Injection presse-papiers** selon le flux design en 5 étapes :
  1. Sauvegarder le presse-papiers courant.
  2. Écrire le texte transcrit dedans (`pyperclip`).
  3. Simuler `Ctrl+V`.
  4. Attendre ~80 ms.
  5. Restaurer le presse-papiers original.
- Déclencheur temporaire (touche fixe codée en dur) pour démarrer/arrêter l'enregistrement le temps de tester.
- Feedback minimal : un `print` ou un bip système à chaque transition.

**Critère de validation :** on appuie, on parle en français, le texte transcrit (accents compris) apparaît au curseur dans n'importe quelle application, et le presse-papiers d'origine est restauré.

**Dépendances :** Phase 0.

---

## Phase 2 — Modes de déclenchement, machine à états et configuration

**Objectif :** les deux modes paramétrables fonctionnent et l'app lit sa config.

**Composants design concernés :** machine à états (Section 3, les deux diagrammes push-to-talk et toggle).

**Tâches :**
- **Hotkey global** propre avec `pynput` (ou `keyboard`), fonctionnant quelle que soit la fenêtre au premier plan.
- **Machine à états explicite** : `Repos → Écoute → Transcription → Injection → Repos`, avec gestion des transitions illégales.
- **Mode push-to-talk** : `Repos → Écoute` au maintien de la touche, le relâchement déclenche la transcription.
- **Mode toggle** : premier appui démarre l'écoute, second appui l'arrête et déclenche la transcription. Anti-rebond pour éviter les doubles déclenchements.
- **Lecture de la configuration** : mode, touche de raccourci, modèle, device, `compute_type`, langue, périphérique micro — tous lus depuis le fichier de config (valeurs par défaut : `push-to-talk`, `Ctrl+Espace`, `large-v3`, `cuda`, `float16`, `Français`).
- Rechargement de la config sans redémarrer si possible.

**Critère de validation :** on bascule entre push-to-talk et toggle via le fichier de config, et chacun se comporte conformément aux deux diagrammes de la machine à états.

**Dépendances :** Phase 1.

---

## Phase 3 — Robustesse presse-papiers, accents et cas limites

**Objectif :** fiabiliser la partie la plus fragile avant d'empiler l'UI.

**Composants design concernés :** flux presse-papiers (Section 3) avec sa garantie « accents é à ç toujours corrects · contenu utilisateur préservé ».

**Tâches :**
- Tester l'injection sur tous les accents et caractères français (é, è, ê, à, â, ç, ù, û, ô, î, ï, œ, guillemets « »).
- Gérer le presse-papiers non-texte (image, fichiers copiés) : sauvegarder/restaurer sans le corrompre, ou détecter et restaurer proprement.
- Caler le délai d'attente (~80 ms) : trop court = collage manqué, trop long = latence perçue. Le rendre configurable.
- Gérer les transcriptions vides (silence, bruit) : ne rien coller.
- Gérer les très longues dictées (buffer audio volumineux).
- Gérer l'enchaînement rapide de plusieurs dictées sans collision d'état ou de presse-papiers.

**Critère de validation :** 20 dictées variées d'affilée (courtes, longues, avec accents, avec presse-papiers préexistant) sans perte de texte ni corruption du presse-papiers.

**Dépendances :** Phase 2.

---

## Phase 4 — Systray, icônes d'état et notifications

**Objectif :** première couche visible, simple et robuste, avant la pilule.

**Composants design concernés :** Section 4 (icônes systray : repos / écoute / traitement / erreur) et Section 5 (notifications toast : prêt / GPU absent → repli / erreur micro). Menu systray (Settings variante C).

**Tâches :**
- Icône systray avec `pystray`, reflétant l'état courant via 4 visuels : **repos** (mic gris), **écoute** (mic rouge), **traitement** (spinner bleu), **erreur** (⚠ orange). Format 16×16 / 32×32.
- **Menu clic-droit** (Settings C) : ligne d'état (« État : prêt »), accès « Paramètres… », bascule rapide du mode, sélection de langue, « Quitter ».
- **Notifications toast** (Section 5), ~4 s :
  - succès au démarrage : « Voix→Clavier prêt — modèle large-v3 chargé sur GPU » ;
  - repli : « GPU introuvable — repli sur CPU, modèle tiny » ;
  - erreur micro : « Erreur micro — aucun périphérique détecté ».
- Brancher l'icône et les toasts sur la machine à états de la Phase 2.

**Critère de validation :** l'icône change de façon fiable à chaque transition d'état, le menu permet de changer de mode et de quitter, les trois toasts s'affichent dans leurs conditions respectives.

**Dépendances :** Phases 2 et 3.

---

## Phase 5 — Pilule flottante

**Objectif :** la « pilule » always-on-top, cœur de l'expérience visuelle.

**Composants design concernés :** Section 1 — 4 variantes (A barre minimale, B forme d'onde, C badge circulaire, D pilule complète avec timer), 3 états (repos / écoute / transcription).

**Tâches :**
- Fenêtre flottante avec `PyQt`/`PySide` : sans bordure, always-on-top, fond translucide, repositionnable par glisser, persistante en position.
- Architecture **commune** rendant l'état (repos / écoute / transcription), avec les variantes interchangeables par configuration (le panneau de paramètres expose « Pilule : Variante ▾ »). **Seule la variante D est implémentée dans cette phase ; A, B et C sont différées** mais le sélecteur et le point d'extension restent prévus :
  - **D — Pilule complète (par défaut, à réaliser)** : fond plein coloré, timer d'enregistrement (0:04…), waveform réagissant au volume réel du micro, le plus « présent ». 3 états : repos (contour pointillé discret), écoute (fond rouge + timer + waveform), transcription (fond bleu + spinner).
  - **A — Barre minimale** *(différée)* : point coloré + texte, point rouge pulsé en écoute.
  - **B — Forme d'onde** *(différée)* : icône micro + barres d'onde + spinner.
  - **C — Badge circulaire ~50×50px** *(différée)* : anneau coloré + halo pulsé, anneau bleu tournant en transcription.
- Animation pour la variante D : pulsation en écoute, spinner en transcription, niveau du micro en temps réel et timer.
- Fonctionnement correct en **multi-écrans** (rester sur l'écran choisi, ne pas disparaître).

**Critère de validation :** la variante D affiche correctement les 3 états et suit la machine à états en temps réel (timer et waveform inclus) ; la pilule reste visible et repositionnable sur un setup multi-écrans.

**Dépendances :** Phase 4 (état déjà exposé proprement).

---

## Phase 6 — Fenêtre de paramètres

**Objectif :** configuration complète via interface graphique.

**Composants design concernés :** Section 2 — variante A (carte compacte), retenue comme interface principale. La fenêtre à onglets (B) n'est pas réalisée. Le menu systray (C) y mène déjà depuis la Phase 4.

**Tâches :**
- Fenêtre de paramètres : **carte compacte (variante A)** comme interface principale (fenêtre modale flottante ~320×360px, barre de titre sombre, fermeture par ✕), exposant tous les réglages :
  - **Déclenchement** : mode (push-to-talk / toggle ▾), raccourci (capture de la combinaison de touches).
  - **Transcription / Modèle** : modèle (`large-v3` ▾ + options légères), device (CUDA / CPU ▾), `compute_type`, langue (Français ▾ / auto-détection).
  - **Micro** : sélection du périphérique d'entrée, délai de restauration du presse-papiers.
  - **Interface** : choix de la variante de pilule (A/B/C/D ▾), position (bas-droite ▾, etc.).
  - **Démarrage** : lancer au démarrage de Windows (☑), afficher l'icône systray (☑).
- Boutons **Annuler / Enregistrer** ; écriture dans le fichier de config ; application à chaud quand c'est possible (sinon, indiquer qu'un redémarrage est requis, p. ex. pour un changement de modèle).
- Validation des entrées (raccourci en conflit, modèle indisponible, device absent).

**Critère de validation :** chaque réglage modifié depuis l'interface se reflète dans le comportement de l'app et persiste après redémarrage.

**Dépendances :** Phase 5 (la variante de pilule est l'un des réglages).

---

## Phase 7 — Démarrage Windows, gestion d'erreurs et repli

**Objectif :** comportement robuste en conditions réelles.

**Composants design concernés :** notifications de repli/erreur (Section 5), état « erreur » du systray (Section 4).

**Tâches :**
- **Lancement au démarrage** de Windows (clé de registre `Run` ou dossier Démarrage), piloté par le réglage de la Phase 6.
- **Repli GPU → CPU** : si CUDA indisponible, basculer sur un modèle léger en CPU et notifier l'utilisateur (toast « GPU introuvable — repli »). Les trois modèles (`tiny`, `base`, `small`) sont disponibles dans la config ; le repli automatique part du plus léger (`tiny`) par sécurité, le plus efficace sera retenu après test.
- **Repli VRAM** : si `float16` échoue faute de mémoire, tenter `int8_float16`.
- **Erreur micro** : aucun périphérique → toast d'erreur + icône systray en état erreur, sans planter.
- Gestion des exceptions sur tout le cycle (capture échouée, transcription échouée, collage échoué) avec retour à l'état Repos propre.
- Journalisation (log) pour le diagnostic.

**Critère de validation :** débrancher le micro, simuler l'absence de GPU, saturer la VRAM → l'app dégrade proprement et informe l'utilisateur sans crash.

**Dépendances :** Phases 4 et 6.

---

## Phase 8 — Packaging, multi-écrans / Quest 3 et tests finaux

**Objectif :** une application installable et validée sur le cas d'usage réel.

**Tâches :**
- **Packaging** en exécutable Windows (`PyInstaller` ou équivalent), en embarquant correctement les dépendances natives (CTranslate2, CUDA, modèle ou téléchargement au premier lancement).
- Gestion du **téléchargement du modèle** au premier lancement avec retour visuel (le modèle `large-v3` est volumineux).
- Vérifier l'injection sur fenêtres situées sur **écrans secondaires** et dans une configuration **Quest 3 multi-écrans virtuels**.
- Tests d'intégration de bout en bout sur les deux modes, les 4 variantes de pilule, les chemins d'erreur.
- Vérifier le besoin éventuel de **droits administrateur** (selon la bibliothèque de hotkey) et documenter le lancement.
- Documentation utilisateur courte (installation, raccourci par défaut, réglages).

**Critère de validation :** installation propre sur une machine, dictée fonctionnelle dans une interface IA sur écran principal et sur écran virtuel Quest 3, dans les deux modes.

**Dépendances :** toutes les phases précédentes.

---

## Ordre de construction (résumé)

```
Phase 0  Environnement + GPU + modèle
   ↓
Phase 1  Cœur audio→texte→injection (sans UI)        ← partie risquée, validée en 1er
   ↓
Phase 2  Modes (push-to-talk + toggle) + machine à états + config
   ↓
Phase 3  Robustesse presse-papiers + accents + cas limites
   ↓
Phase 4  Systray : icônes d'état + menu + notifications
   ↓
Phase 5  Pilule flottante (4 variantes, 3 états)
   ↓
Phase 6  Fenêtre de paramètres (tous les réglages)
   ↓
Phase 7  Démarrage Windows + erreurs + repli GPU/CPU/VRAM
   ↓
Phase 8  Packaging + multi-écrans/Quest 3 + tests finaux
```

---

## Correspondance composants design → phases

| Composant design (wireframes) | Phase(s) |
|-------------------------------|----------|
| Machine à états (Section 3, push-to-talk + toggle) | 2 |
| Flux presse-papiers en 5 étapes (Section 3) | 1, 3 |
| Icônes systray : repos / écoute / traitement / erreur (Section 4) | 4 |
| Notifications toast : prêt / repli GPU / erreur micro (Section 5) | 4, 7 |
| Menu systray clic-droit (Settings C) | 4 |
| Pilule — 4 variantes A/B/C/D, 3 états (Section 1) | 5 |
| Fenêtre de paramètres — carte compacte A / onglets B (Section 2) | 6 |
| Réglages exposés (mode, raccourci, modèle, device, langue, pilule, position, démarrage) | 6 |

---

## Décisions arrêtées

Ces points sont fixés ; Claude Code doit s'y conformer :

1. **Variante de pilule par défaut : D (pilule complète)**. Seule la variante D est implémentée en Phase 5. Les variantes A, B et C sont **différées** : prévoir l'architecture interchangeable (le sélecteur de variante existe dans les paramètres) mais ne pas les réaliser pour l'instant.
2. **Fenêtre de paramètres : carte compacte (A)**. C'est l'interface principale. La fenêtre à onglets (B) n'est pas réalisée.
3. **Format de config : `toml`**. Plus lisible pour une édition manuelle occasionnelle et compatible avec les commentaires ; round-trip GUI ↔ fichier propre.
4. **Raccourci par défaut : `Ctrl+Espace`**.
5. **Modèles de repli CPU : tous (`tiny`, `base`, `small`)** disponibles dans la configuration ; les plus efficaces seront déterminés par test ultérieur. Le repli automatique en l'absence de GPU partira du plus léger (`tiny`) par sécurité, ajustable ensuite.
