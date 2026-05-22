# jf-dvr bridge installer.
#
# Sets up the Python bridge end to end:
#   - creates the Python virtual environment (.venv)
#   - installs the Python dependencies
#   - creates config.toml from the example (if missing)
#   - registers the Windows scheduled tasks (jf-dvr-bridge + jf-dvr-tray)
#
# Run from an elevated (Administrator) PowerShell in the project root:
#   powershell -NoProfile -File install.ps1
#
# For the Jellyfin plugin, see: tools\install-plugin.ps1
#
# ASCII-only on purpose (Windows PowerShell 5.1 reads a BOM-less .ps1 as the
# system ANSI code page, which corrupts non-ASCII bytes).

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

# --- must be elevated (registering a SYSTEM scheduled task needs admin) ------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error 'Run this from an elevated (Administrator) PowerShell.'
    exit 1
}

# --- 1. locate Python -------------------------------------------------------
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Error 'Python not found on PATH. Install Python 3.11 or newer first.'
    exit 1
}
Write-Output "Using Python: $python"

# --- 2. create the virtual environment --------------------------------------
$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Output 'Creating virtual environment (.venv)...'
    & $python -m venv (Join-Path $root '.venv')
} else {
    Write-Output 'Virtual environment already exists.'
}

# --- 3. install dependencies ------------------------------------------------
Write-Output 'Installing Python dependencies...'
& $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
& $venvPy -m pip install -r (Join-Path $root 'requirements.txt') --disable-pip-version-check

# --- 4. config.toml ---------------------------------------------------------
$config = Join-Path $root 'config.toml'
if (-not (Test-Path $config)) {
    Copy-Item (Join-Path $root 'config.example.toml') $config
    Write-Output 'Created config.toml from config.example.toml.'
    Write-Output '  -> Edit config.toml for your environment (EDCB host/port/folder).'
} else {
    Write-Output 'config.toml already exists, keeping it.'
}

# --- 5. register scheduled tasks --------------------------------------------
Write-Output 'Registering scheduled tasks (jf-dvr-bridge + jf-dvr-tray)...'
& powershell -NoProfile -File (Join-Path $root 'tools\install_service.ps1')

Write-Output ''
Write-Output '=== Bridge install complete ==='
Write-Output 'Check config.toml, then the bridge is reachable at http://127.0.0.1:40880'
Write-Output 'For the Jellyfin plugin: powershell -NoProfile -File tools\install-plugin.ps1'
