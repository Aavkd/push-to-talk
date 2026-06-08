# Voix → Clavier

Dictée vocale **100 % locale** pour Windows : on déclenche un raccourci, on
parle en français, et le texte transcrit s'insère à l'emplacement du curseur
dans n'importe quelle application (interface IA, navigateur, éditeur…).

Transcription via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(`large-v3`) sur GPU NVIDIA (CUDA / float16). Aucune donnée ne quitte la machine.

> État actuel : **Phase 8** terminée (packaging Windows PyInstaller ; téléchargement
> du modèle au 1er lancement avec retour visuel ; chemins inscriptibles dès qu'il
> est packagé ; diagnostic `doctor` + droits admin ; tests d'intégration ; guide
> utilisateur `DOCS.md`). Phases 0 à 7 également terminées (environnement/GPU/CLI ;
> cœur audio → texte → injection ; raccourci global, machine à états, modes, config
> à chaud ; robustesse presse-papiers/accents ; systray + icônes d'état + toasts ;
> pilule flottante ; fenêtre de paramètres ; démarrage Windows + repli GPU/CPU/VRAM
> + journal). **Le guide utilisateur complet est dans [`DOCS.md`](DOCS.md).**

## Arborescence

```
Push to talk/
├─ config.toml              # configuration (format TOML, commentée)
├─ pyproject.toml           # métadonnées + dépendances par phase
├─ requirements.txt         # installation rapide
└─ src/voix_clavier/
   ├─ config.py             # lecture/écriture config.toml          [Phase 0 ✓]
   ├─ transcription.py      # modèle Whisper chargé une fois + STT   [Phase 0 ✓]
   ├─ cli.py                # CLI de validation Phase 0              [Phase 0 ✓]
   ├─ audio.py              # capture micro (sounddevice)           [Phase 1 ✓]
   ├─ injection.py          # injection presse-papiers (Win32)      [Phase 1 ✓]
   ├─ dictate.py            # boucle capture→texte→injection (console) [Phase 1 ✓]
   ├─ hotkey.py             # raccourci global (pynput)             [Phase 2 ✓]
   ├─ state.py              # machine à états explicite             [Phase 2 ✓]
   ├─ engine.py             # orchestration raccourci → dictée      [Phase 2 ✓]
   ├─ watcher.py            # rechargement à chaud de la config     [Phase 2 ✓]
   ├─ clipboard.py          # presse-papiers Win32 (texte + binaire)[Phase 3 ✓]
   ├─ app.py                # application pilotée par le raccourci  [Phase 2+ ✓]
   ├─ autostart.py          # lancement au démarrage de Windows     [Phase 7 ✓]
   ├─ paths.py              # chemins inscriptibles (dev vs packagé)[Phase 8 ✓]
   ├─ models.py             # téléchargement modèle + progression   [Phase 8 ✓]
   ├─ doctor.py             # diagnostic + droits admin             [Phase 8 ✓]
   └─ ui/
      ├─ icons.py           # icônes d'état systray (Pillow)        [Phase 4 ✓]
      ├─ systray.py         # icône systray + menu + toasts         [Phase 4 ✓]
      ├─ pill.py            # pilule flottante always-on-top (D)    [Phase 5 ✓]
      ├─ settings.py        # fenêtre de paramètres (carte A)       [Phase 6 ✓]
      └─ download.py        # progression du téléchargement (Qt)    [Phase 8 ✓]

packaging/                  # PyInstaller (Phase 8)
├─ voix-clavier.spec        # spécification de build (onedir)
├─ entry.py                 # point d'entrée de l'exécutable
├─ runtime_hook_cuda.py     # DLL CUDA trouvables dans l'archive
└─ build.ps1                # script de build
tests/                      # tests d'intégration (sans GPU ni micro)
```

## Packaging Windows (Phase 8)

```powershell
.venv\Scripts\Activate.ps1
pip install -e .[dev]        # PyInstaller + pytest
pytest                       # tests d'intégration
.\packaging\build.ps1        # → dist\VoixClavier\VoixClavier.exe
```

L'exécutable est un dossier **onedir** embarquant les dépendances natives
(CTranslate2, CUDA/cuDNN, PyAV, PySide6). Le modèle `large-v3` (~3 Go) n'est
**pas** embarqué : il est téléchargé au premier lancement avec une fenêtre de
progression, puis mis en cache sous `%LOCALAPPDATA%\VoixClavier\models`. Voir
[`DOCS.md`](DOCS.md) pour le guide utilisateur (réglages, droits admin,
multi-écrans / Quest 3, diagnostic).

## Installation (Phase 0)

Prérequis : Python ≥ 3.11, GPU NVIDIA avec pilotes récents.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Les bibliothèques `nvidia-cublas-cu12` et `nvidia-cudnn-cu12` fournissent le
runtime CUDA/cuDNN requis par CTranslate2 ; aucune installation CUDA système
séparée n'est nécessaire.

## Utilisation (CLI Phase 0)

```powershell
# GPU (défaut, lu depuis config.toml)
python -m voix_clavier.cli "Enregistrement-_2_.wav"

# Forcer un repli CPU léger pour comparer
python -m voix_clavier.cli "Enregistrement-_2_.wav" --device cpu --compute-type int8 --model small
```

Le premier lancement télécharge le modèle (`large-v3` est volumineux, ~3 Go) ;
les suivants utilisent le cache local.

## Critère de validation Phase 0

Un `.wav` français (avec accents) est transcrit correctement, sur GPU, en
moins de ~2 s pour quelques phrases. La sortie indique la langue détectée, la
durée audio, le temps de transcription et le RTF (real-time factor).

## Utilisation (boucle Phase 1)

La Phase 1 valide la boucle complète **micro → transcription → collage** sans
interface. Le raccourci global propre arrive en Phase 2 ; ici le déclencheur est
**temporaire** et piloté depuis la console (touche Entrée).

```powershell
pip install -r requirements.txt   # installe sounddevice, numpy, pyperclip

# Lister les micros disponibles (pour renseigner [micro] peripherique dans config.toml)
python -m voix_clavier.dictate --list-devices

# Lancer la boucle de dictée
python -m voix_clavier.dictate
```

Déroulé d'une dictée :

1. Entrée pour **démarrer** l'enregistrement (bip aigu).
2. On parle ; Entrée pour **arrêter** (bip grave) → transcription sur GPU.
3. Un court compte à rebours (`--focus-delay`, 2 s par défaut) laisse le temps de
   **cliquer dans la fenêtre cible** — nécessaire car c'est la console qui a le
   focus tant que le raccourci global (Phase 2) n'existe pas.
4. Le texte est collé au curseur via le presse-papiers, qui est ensuite restauré.

Le modèle Whisper est chargé **une seule fois** au démarrage de la boucle et
réutilisé pour toutes les dictées.

### Critère de validation Phase 1

On appuie, on parle en français, le texte transcrit (accents compris) apparaît
au curseur dans n'importe quelle application, et le presse-papiers d'origine est
restauré intact.

## Utilisation (Phase 2 — raccourci global)

La Phase 2 supprime la gymnastique console : la dictée se déclenche **partout
dans le système** via un raccourci global (défaut `Ctrl+Espace`), dans l'un des
deux modes lus depuis `config.toml`.

```powershell
pip install -r requirements.txt   # installe aussi pynput

# Lancer l'application (mode et raccourci lus depuis config.toml)
python -m voix_clavier.app

# Surcharges ponctuelles sans éditer la config
python -m voix_clavier.app --mode toggle --hotkey ctrl+alt+s
```

Modes (`[declenchement] mode` dans `config.toml`) :

- **`push-to-talk`** (défaut) : on **maintient** le raccourci pour parler ; le
  **relâchement** déclenche la transcription puis le collage.
- **`toggle`** : un **premier appui** démarre l'écoute, un **second** l'arrête et
  transcrit. Un anti-rebond (~300 ms) évite les doubles déclenchements.

La machine à états explicite (`Repos → Écoute → Transcription → Injection →
Repos`, plus un état `Erreur`) garde-fou contre les transitions illégales et les
chevauchements : tant qu'une dictée n'est pas revenue à `Repos`, une nouvelle
activation est ignorée.

**Rechargement à chaud** : modifier `config.toml` pendant que l'app tourne
applique immédiatement le mode, le raccourci, le micro, la langue et le délai de
presse-papiers. Le changement de modèle / device / `compute_type` exige un
redémarrage (le modèle Whisper est lourd à recharger) — un message le signale.

### Critère de validation Phase 2

On bascule entre `push-to-talk` et `toggle` via le fichier de config, et chacun
se comporte conformément aux deux diagrammes de la machine à états.

## Utilisation (Phase 5 — pilule flottante)

À partir de la Phase 5, `python -m voix_clavier.app` affiche une **pilule
flottante** always-on-top (variante D, « pilule complète ») qui suit la machine à
états en temps réel :

- **repos** : pilule quasi-invisible (contour pointillé, micro gris) ;
- **écoute** : fond rouge plein, **timer** d'enregistrement et **waveform**
  réagissant au volume réel du micro ;
- **transcription** : fond bleu plein, spinner + « traitement… ».

```powershell
pip install -r requirements.txt   # installe aussi PySide6

python -m voix_clavier.app         # pilule + systray
python -m voix_clavier.app --no-pill      # systray seul (Phase 4)
python -m voix_clavier.app --no-pill --no-systray   # mode console
```

La pilule est **repositionnable par glisser** ; sa position est mémorisée et
restaurée au lancement suivant. Sur un montage **multi-écrans** (y compris écran
virtuel Quest 3), une position devenue hors-champ est ignorée au profit de
l'ancrage par défaut — la pilule ne disparaît jamais. **Clic droit** sur la
pilule pour « Quitter » (même sans icône systray).

Réglages dans `config.toml`, section `[interface]` :

- `variante_pilule` : `"D"` (pilule complète, défaut), `"A"` (barre minimale),
  `"B"` (forme d'onde) ou `"C"` (badge circulaire ~50×50) ; changeable à chaud ;
- `position_pilule` : ancrage par défaut (`bas-droite`, `bas-gauche`,
  `haut-droite`, `haut-gauche`, `centre`).

### Critère de validation Phase 5

La variante D affiche correctement les 3 états et suit la machine à états en
temps réel (timer et waveform inclus) ; la pilule reste visible et
repositionnable sur un setup multi-écrans.
