# Construit l'exécutable Windows Voix -> Clavier (Phase 8).
#
# Prerequis : environnement virtuel actif avec les dependances installees
#   pip install -r requirements.txt
#   pip install pyinstaller
#
# Usage (depuis la racine du projet) :
#   .\packaging\build.ps1
#
# Resultat : dist\VoixClavier\VoixClavier.exe (dossier distribuable onedir).

$ErrorActionPreference = "Stop"

# Se placer a la racine du projet (parent de packaging\).
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> Verification de PyInstaller..."
python -c "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"
if (-not $?) {
    Write-Error "PyInstaller introuvable. Installez-le : pip install pyinstaller"
    exit 1
}

Write-Host "==> Nettoyage des sorties precedentes..."
if (Test-Path "$root\build")        { Remove-Item -Recurse -Force "$root\build" }
if (Test-Path "$root\dist\VoixClavier") { Remove-Item -Recurse -Force "$root\dist\VoixClavier" }

Write-Host "==> Build PyInstaller (onedir)..."
pyinstaller "packaging\voix-clavier.spec" --noconfirm

if ($?) {
    Write-Host ""
    Write-Host "OK -> dist\VoixClavier\VoixClavier.exe"
    Write-Host "Premier lancement : telechargement du modele (~3 Go pour large-v3)."
} else {
    Write-Error "Echec du build."
    exit 1
}
