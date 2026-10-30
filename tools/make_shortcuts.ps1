# Create (or remove) the Citizen Assistant shortcuts.
#
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1 -Startup
#   powershell -ExecutionPolicy Bypass -File tools\make_shortcuts.ps1 -Remove
#
# The shortcut runs pythonw.exe with an absolute path to launcher.py, so it
# always imports the app package sitting next to *this* script.  The previous
# "-m app" form depended on the working directory, which meant a shortcut left
# behind by an older install kept launching the old code from Start menu
# search even after a new copy was downloaded.
param(
    [string]$ProjectDir = (Split-Path -Parent $PSScriptRoot),
    [switch]$Startup,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# A caller may still pass "%~dp0", which ends in a backslash: "C:\path\" is
# read as an escaped quote and arrives with a stray quote, which Resolve-Path
# rejects as an illegal path.  Strip that first, then resolve with -LiteralPath
# so a folder containing [ or ] is not treated as a wildcard pattern.
$ProjectDir = $ProjectDir.Trim().Trim('"').TrimEnd('\')
if (-not (Test-Path -LiteralPath $ProjectDir)) {
    Write-Error "Project folder not found: '$ProjectDir'"
}
$ProjectDir = (Resolve-Path -LiteralPath $ProjectDir).Path.TrimEnd('\')

$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$startupDir = [Environment]::GetFolderPath('Startup')
$allUsers = if ($env:ProgramData) {
    Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs'
} else { $null }

# Every place Start menu search and the desktop look for the app.  An older
# install may have left a shortcut in any of them.
$searchDirs = @($desktop, $programs, $startupDir, $allUsers) |
    Where-Object { $_ -and (Test-Path $_) } |
    Select-Object -Unique

$name = 'Citizen Assistant.lnk'

function Get-LinkArguments([string]$path) {
    $shell = New-Object -ComObject WScript.Shell
    return [string]($shell.CreateShortcut($path).Arguments)
}

if ($Remove) {
    # Always drop the startup entry too: an uninstalled overlay must not try to
    # launch itself at login.
    foreach ($dir in $searchDirs) {
        $path = Join-Path $dir $name
        if (-not (Test-Path $path)) { continue }
        try {
            Remove-Item $path -Force -ErrorAction Stop
            Write-Host "Removed $path"
        } catch {
            Write-Warning "Could not remove $path : $($_.Exception.Message)"
        }
    }
    exit 0
}

$launcher = Join-Path $ProjectDir 'launcher.py'
if (-not (Test-Path $launcher)) {
    Write-Error "launcher.py not found in $ProjectDir - run install.bat from the app folder."
}

$pythonw = Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'
if (Test-Path 'C:\scov\Scripts\pythonw.exe') {
    $pythonw = 'C:\scov\Scripts\pythonw.exe'
}
if (-not (Test-Path $pythonw)) {
    Write-Error "pythonw.exe not found. Run setup.bat first."
}

$arguments = '"' + $launcher + '"'

# Remove shortcuts that point at a different folder before writing ours.
# Windows search lists them all, and the stale one often wins.
foreach ($dir in $searchDirs) {
    $path = Join-Path $dir $name
    if (-not (Test-Path $path)) { continue }
    try {
        if ((Get-LinkArguments $path) -ne $arguments) {
            Remove-Item $path -Force -ErrorAction Stop
            Write-Host "Removed stale shortcut $path"
        }
    } catch {
        Write-Warning "Could not update $path : $($_.Exception.Message)"
    }
}

$targets = @(
    (Join-Path $desktop $name),
    (Join-Path $programs $name)
)
if ($Startup) { $targets += (Join-Path $startupDir $name) }

$icon = Join-Path $ProjectDir 'app.ico'

foreach ($path in $targets) {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = $arguments
    $lnk.WorkingDirectory = $ProjectDir
    $lnk.Description = "Star Citizen overlay - press F3 ($ProjectDir)"
    if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
    $lnk.Save()
    Write-Host "Created $path"
}
