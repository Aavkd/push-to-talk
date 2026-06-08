# Spécification technique — Outil de dictée vocale "voix vers clavier" (Windows)

## Objectif

Construire une application desktop Windows qui transforme la voix en texte injecté à l'emplacement du curseur, partout dans le système. L'utilisateur déclenche un raccourci, parle, et le texte transcrit s'insère dans le champ actif (interface IA, navigateur, éditeur, etc.), comme les apps Superwhisper / Wispr Flow.

Cas d'usage principal : dicter des prompts à des interfaces IA, y compris dans des configurations multi-écrans (ex. casque Quest 3 avec écrans virtuels).

## Contraintes et décisions déjà arrêtées

- **OS cible : Windows** (uniquement, pas besoin de portabilité macOS/Linux).
- **Transcription 100% locale** — aucune donnée ne doit sortir de la machine. Pas d'API cloud.
- **GPU disponible : NVIDIA RTX 4090 mobile** — on peut viser le modèle Whisper `large-v3` avec accélération CUDA et une latence faible.
- **Langage : Python.**
- **Deux modes de déclenchement, sélectionnables dans les paramètres :**
  - **Push-to-talk** : enregistrement tant que la touche est maintenue, transcription au relâchement.
  - **Toggle** : un appui démarre l'enregistrement, un second l'arrête et déclenche la transcription.
- **Langue de dictée principale : français** (donc gestion correcte des accents impérative). Idéalement multilingue / auto-détection configurable.
- Priorité au fonctionnement local et privé sur la simplicité.

## Architecture fonctionnelle

Flux nominal :

1. L'utilisateur déclenche le raccourci global (push-to-talk maintenu, ou toggle).
2. Le micro est capturé dans un buffer audio.
3. À l'arrêt (relâchement en push-to-talk, second appui en toggle), le buffer est transcrit en texte localement via Whisper.
4. Le texte est inséré à l'emplacement du curseur via le presse-papiers + collage automatique (Ctrl+V).
5. Une petite UI flottante ("pilule") indique l'état courant (repos / écoute / transcription).

## Stack technique recommandée

| Brique | Bibliothèque | Notes |
|--------|-------------|-------|
| Hotkey global | `pynput` (ou `keyboard`) | `pynput` plus propre ; `keyboard` plus simple mais demande souvent les droits admin. Doit fonctionner quelle que soit l'app au premier plan. |
| Capture micro | `sounddevice` | Basé sur PortAudio. Enregistre dans un buffer numpy directement exploitable par Whisper. |
| STT local | `faster-whisper` | Réimplémentation de Whisper via CTranslate2, ~4× plus rapide à précision égale. Doit tourner sur GPU CUDA (`device="cuda"`, `compute_type="float16"`). Modèle cible : `large-v3`. |
| Injection de texte | `pyperclip` + simulation `Ctrl+V` (`pynput`/`keyboard`) | Méthode presse-papiers obligatoire pour fiabilité avec les accents français. Sauvegarder puis restaurer le contenu précédent du presse-papiers. |
| UI "pilule" | `pystray` (démarrage simple) puis `PyQt`/`PySide` (vraie fenêtre flottante always-on-top) | Voir plan par étapes ci-dessous. |

## Détails techniques importants

### Injection de texte (point sensible)
Ne **pas** simuler des frappes caractère par caractère : c'est lent et fragile avec les caractères accentués (é, à, ç, etc.), critiques en français. Méthode robuste :
1. Sauvegarder le contenu actuel du presse-papiers.
2. Écrire le texte transcrit dans le presse-papiers.
3. Simuler `Ctrl+V`.
4. Restaurer le contenu original du presse-papiers (après un court délai pour que le collage soit pris en compte).

### Transcription locale
- Charger le modèle `large-v3` une seule fois au démarrage et le garder en mémoire (le chargement est coûteux, la transcription ne doit pas le recharger à chaque dictée).
- Configuration GPU : `WhisperModel("large-v3", device="cuda", compute_type="float16")`.
- Prévoir le repli `compute_type="int8_float16"` si la VRAM est insuffisante.
- Langue : paramétrable (français par défaut), avec option d'auto-détection.

### Modes de déclenchement
- Le mode (push-to-talk / toggle) et la touche de raccourci doivent être lus depuis un fichier de configuration (ex. `config.json` ou `config.toml`).
- Gérer proprement la machine à états : repos → écoute → transcription → injection → repos.
- En toggle, gérer le cas où l'utilisateur arrête l'enregistrement : éviter les doubles déclenchements.

### UI "pilule"
- Petite fenêtre flottante, sans bordure, toujours au-dessus des autres (always-on-top), repositionnable.
- États visuels distincts : repos, en écoute (enregistrement), transcription en cours.
- Doit rester visible et fonctionnelle en configuration multi-écrans.

## Plan de construction par étapes (validation incrémentale)

Construire et valider le cœur avant d'ajouter l'UI. Ordre recommandé :

1. **Prototype minimal sans UI** : hotkey → enregistre le micro → transcrit avec faster-whisper → colle le texte au curseur. Feedback minimal (un son ou un print). C'est la partie risquée, à valider en premier.
2. **Ajout des deux modes** (push-to-talk + toggle) et du fichier de configuration.
3. **Gestion robuste du presse-papiers** (sauvegarde/restauration) et des accents.
4. **UI pilule** : d'abord une icône systray (`pystray`) pour l'état, puis une vraie fenêtre flottante (`PyQt`/`PySide`).
5. **Finitions** : paramètres (choix du modèle, langue, device, raccourci, mode), démarrage automatique avec Windows, gestion des erreurs (micro indisponible, GPU absent → repli CPU).

## Paramètres à exposer dans la configuration

- Mode de déclenchement : push-to-talk / toggle.
- Touche(s) de raccourci.
- Modèle Whisper (`large-v3` par défaut, options plus légères pour repli).
- Device (`cuda` / `cpu`) et `compute_type`.
- Langue de dictée (français par défaut / auto-détection).
- Périphérique micro (si plusieurs).
- Position et apparence de la pilule.

## Points de vigilance

- **Accents français** : valider explicitement que é, è, à, ç, ù, ô etc. s'insèrent correctement (raison du choix presse-papiers plutôt que frappe simulée).
- **Restauration du presse-papiers** : ne pas écraser durablement ce que l'utilisateur avait copié.
- **Chargement unique du modèle** : ne jamais recharger le modèle à chaque dictée.
- **Repli sans GPU** : prévoir un comportement dégradé propre si CUDA n'est pas disponible (modèle plus léger sur CPU).
- **Droits administrateur** : selon la bibliothèque de hotkey choisie, l'app peut nécessiter les droits admin pour capter le raccourci globalement et injecter dans toutes les fenêtres.
- **Multi-écrans / Quest 3** : vérifier que l'injection fonctionne sur les fenêtres situées sur des écrans secondaires/virtuels.
