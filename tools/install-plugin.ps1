# Install / update the jf-dvr Jellyfin plugin.
#
# Copies the prebuilt plugin into Jellyfin's plugins folder. The plugin DLL is
# locked while Jellyfin runs, so STOP Jellyfin before running this, then start
# it again afterwards.
#
#   1. Stop Jellyfin
#   2. powershell -NoProfile -File tools\install-plugin.ps1
#   3. Start Jellyfin
#
# ASCII-only on purpose (Windows PowerShell 5.1 reads a BOM-less .ps1 as the
# system ANSI code page, which corrupts non-ASCII bytes).

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Refuse to run while Jellyfin is up (its plugin DLL would be locked)
if (Get-Process -Name 'jellyfin' -ErrorAction SilentlyContinue) {
    Write-Error 'Jellyfin is running. Stop it first, then re-run this script.'
    exit 1
}

# Source: prefer the prebuilt release package, fall back to a fresh build
$pkg = Join-Path $root 'release\jf-dvr_1.0.1.0'
if (-not (Test-Path (Join-Path $pkg 'Jellyfin.Plugin.JfDvr.dll'))) {
    $pkg = Join-Path $root 'plugin\bin\Release\net9.0'
}
$dll = Join-Path $pkg 'Jellyfin.Plugin.JfDvr.dll'
if (-not (Test-Path $dll)) {
    Write-Error "Plugin DLL not found. Build the plugin or use the release/ package."
    exit 1
}

# Destination: Jellyfin's plugins folder
$pluginsDir = Join-Path $env:ProgramData 'Jellyfin\Server\plugins'
if (-not (Test-Path $pluginsDir)) {
    Write-Error "Jellyfin plugins folder not found: $pluginsDir"
    exit 1
}

# Remove any older jf-dvr plugin folders to avoid a duplicate load
Get-ChildItem $pluginsDir -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like 'jf-dvr*' -and $_.Name -ne 'jf-dvr_1.0.1.0' } |
    ForEach-Object {
        Write-Output ('Removing old plugin folder: ' + $_.Name)
        Remove-Item $_.FullName -Recurse -Force
    }

$dest = Join-Path $pluginsDir 'jf-dvr_1.0.1.0'
New-Item -ItemType Directory -Force -Path $dest | Out-Null

Copy-Item $dll (Join-Path $dest 'Jellyfin.Plugin.JfDvr.dll') -Force
Write-Output ('Installed: ' + (Join-Path $dest 'Jellyfin.Plugin.JfDvr.dll'))

# Copy meta.json only if absent (Jellyfin maintains its own copy once created)
$destMeta = Join-Path $dest 'meta.json'
$srcMeta = Join-Path $pkg 'meta.json'
if ((-not (Test-Path $destMeta)) -and (Test-Path $srcMeta)) {
    Copy-Item $srcMeta $destMeta -Force
    Write-Output ('Installed: ' + $destMeta)
}

Write-Output ''
Write-Output 'Plugin installed. Start Jellyfin now.'
