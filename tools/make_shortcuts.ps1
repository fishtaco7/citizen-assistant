# Create (or remove) the Citizen Assistant shortcuts.
#
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1 -Startup
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1 -Remove
#
# The shortcut targets pythonw.exe directly, so no console window ever opens.
param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$Startup,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path $ProjectDir).Path.TrimEnd('\')

$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$startupDir = [Environment]::GetFolderPath('Startup')

$targets = @(
    (Join-Path $desktop 'Citizen Assistant.lnk'),
    (Join-Path $programs 'Citizen Assistant.lnk')
)
if ($Startup) { $targets += (Join-Path $startupDir 'Citizen Assistant.lnk') }

if ($Remove) {
    foreach ($path in $targets) {
        if (Test-Path $path) {
            Remove-Item $path -Force
            Write-Host "Removed $path"
        }
    }
    exit 0
}

$pythonw = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'
if (Test-Path 'C:\scov\Scripts\pythonw.exe') {
    $pythonw = 'C:\scov\Scripts\pythonw.exe'
}
if (-not (Test-Path $pythonw)) {
    Write-Error "pythonw.exe not found. Run setup.bat first."
}

$icon = Join-Path $ProjectDir 'app.ico'

foreach ($path in $targets) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = '-m app'
    $lnk.WorkingDirectory = $ProjectDir
    $lnk.Description = 'Star Citizen overlay - press F3'
    if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
    $lnk.Save()
    Write-Host "Created $path"
}

