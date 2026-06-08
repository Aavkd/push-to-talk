# Voix → Clavier — Guide utilisateur

Dictée vocale **100 % locale** pour Windows. On appuie sur un raccourci, on parle
en français, le texte transcrit s'insère à l'emplacement du curseur dans
n'importe quelle application (interface IA, navigateur, éditeur…). Aucune donnée
ne quitte la machine.

---

## Installation

### A. Exécutable packagé (recommandé pour l'utilisateur final)

1. Récupérer le dossier `VoixClavier\` (produit par `packaging\build.ps1`).
2. Lancer `VoixClavier.exe`.
3. **Premier lancement** : le modèle de transcription (`large-v3`, ~3 Go) est
   téléchargé automatiquement. Une fenêtre de progression s'affiche ; le modèle
   est ensuite mis en cache sous `%LOCALAPPDATA%\VoixClavier\models` et les
   lancements suivants sont immédiats.

Aucune installation de Python ou de CUDA séparée n'est nécessaire : tout est
embarqué dans le dossier.

### B. Depuis les sources (développement)

Prérequis : Python ≥ 3.11, GPU NVIDIA avec pilotes récents.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m voix_clavier.app
```

---

## Utilisation

1. **Raccourci par défaut : `Ctrl+Espace`** (configurable).
2. Deux modes (réglables) :
   - **Push-to-talk** : on **maintient** le raccourci pour parler ; le
     **relâchement** déclenche la transcription puis le collage.
   - **Toggle** : un **premier appui** démarre l'écoute, un **second** l'arrête
     et transcrit.
3. La **pilule flottante** always-on-top indique l'état en temps réel (repos /
   écoute avec timer + waveform / transcription). On peut la déplacer par
   glisser ; sa position est mémorisée.
4. L'**icône de la barre des tâches** (systray) reflète aussi l'état et donne
   accès, par clic droit, aux paramètres, au mode, à la langue et à « Quitter ».

---

## Réglages

Tout se règle depuis la **fenêtre de paramètres** (clic droit sur la pilule ou
l'icône systray → « Paramètres… ») ou en éditant `config.toml`.

| Réglage | Valeurs | Application |
|---|---|---|
| Mode | push-to-talk / toggle | à chaud |
| Raccourci | ex. `ctrl+space` | à chaud |
| Modèle | `large-v3`, `small`, `base`, `tiny` | redémarrage |
| Device | `cuda` / `cpu` | redémarrage |
| `compute_type` | `float16`, `int8_float16`, `int8` | redémarrage |
| Langue | `fr`, `en`, … ou auto | à chaud |
| Micro | périphérique d'entrée | à chaud |
| Délai presse-papiers | ms (défaut 80) | à chaud |
| Pilule | variante A/B/C/D, position | à chaud |
| Lancer au démarrage | oui / non | immédiat (registre Windows) |

Emplacement de `config.toml` :
- **packagé** : `%LOCALAPPDATA%\VoixClavier\config.toml` ;
- **sources** : à la racine du projet.

---

## Lancement au démarrage de Windows

Activable dans les paramètres (« Lancer au démarrage de Windows »). L'application
s'inscrit dans la clé de registre `Run` de l'utilisateur courant
(`HKCU\…\CurrentVersion\Run`) — **aucun droit administrateur requis**.

---

## Droits administrateur

Le raccourci global et l'injection de texte fonctionnent **sans élévation** pour
les applications standard.

Exception : une application cible elle-même lancée **en administrateur** (UAC)
n'accepte pas les entrées d'un processus non élevé. Si la dictée ne s'insère pas
dans une fenêtre précise (rare), lancer `VoixClavier.exe` **en tant
qu'administrateur** (clic droit → « Exécuter en tant qu'administrateur ») résout
ce cas particulier.

---

## Multi-écrans et casque Quest 3

- La pilule reste **sur l'écran choisi** et ne disparaît pas en configuration
  multi-écrans ; une position devenue hors-champ est ignorée au profit de
  l'ancrage par défaut.
- L'injection de texte vise la **fenêtre active**, où qu'elle soit : elle
  fonctionne donc sur les écrans secondaires et sur les **écrans virtuels du
  Quest 3** (Virtual Desktop / Quest Link), exactement comme sur l'écran
  principal.
- Le micro du Quest 3 (via Virtual Desktop) apparaît dans la liste des
  périphériques d'entrée et peut être sélectionné dans les paramètres.

---

## Diagnostic et dépannage

En cas de problème, lancer le diagnostic :

```powershell
# depuis les sources
python -m voix_clavier.doctor
# ou, packagé : VoixClavier-doctor.exe (si exposé), sinon voir le journal
```

Il résume l'environnement : mode (sources/packagé), droits administrateur,
chemins (config / journal / cache modèle), disponibilité GPU/CUDA, présence du
modèle en cache, et périphériques micro détectés.

**Journal** (utile pour un rapport de bug) :
`%LOCALAPPDATA%\VoixClavier\logs\voix-clavier.log`.

Replis automatiques (sans intervention) :
- **GPU absent** → modèle léger en CPU + notification ;
- **mémoire GPU insuffisante** (`float16`) → `int8_float16` ;
- **micro absent** → notification d'erreur, l'app ne plante pas.

---

## Construire l'exécutable (mainteneurs)

```powershell
.venv\Scripts\Activate.ps1
pip install -e .[dev]       # ajoute PyInstaller + pytest
.\packaging\build.ps1       # → dist\VoixClavier\VoixClavier.exe
```

Le modèle n'est **pas** embarqué (téléchargé au premier lancement). Tester avant
distribution :

```powershell
pytest          # tests d'intégration (sans GPU ni micro)
python -m voix_clavier.doctor
```
