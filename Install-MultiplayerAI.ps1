[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\MultiplayerAI'),
    [switch]$NoLaunch,
    [switch]$NoShortcuts
)

$ErrorActionPreference = 'Stop'
try {
    $source = Join-Path $PSScriptRoot 'MultiplayerAI'
    if (-not (Test-Path -LiteralPath (Join-Path $source 'MultiplayerAI.exe'))) {
        $source = Join-Path $PSScriptRoot 'dist\MultiplayerAI'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $source '_internal\base_library.zip'))) {
        throw 'Extract the entire Windows release ZIP first. The application files must be beside this installer.'
    }
    $source = [IO.Path]::GetFullPath($source).TrimEnd('\')
    $target = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    if ($source.Equals($target, [StringComparison]::OrdinalIgnoreCase) -or
        $source.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $target.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Choose an installation directory separate from the extracted release.'
    }
    if (Test-Path -LiteralPath $target) {
        if (-not (Test-Path -LiteralPath (Join-Path $target 'MultiplayerAI.exe')) -and
            @(Get-ChildItem -LiteralPath $target -Force).Count -gt 0) {
            throw 'The selected directory contains other files. Choose an empty directory.'
        }
    }
    $installedExe = Join-Path $target 'MultiplayerAI.exe'
    $running = Get-Process -Name MultiplayerAI -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.Equals($installedExe, [StringComparison]::OrdinalIgnoreCase) }
    if ($running) { throw 'Close Multiplayer AI before installing or updating it.' }
    Write-Host 'Installing Multiplayer AI for your Windows account...'
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse -Force
    }
    $exe = Join-Path $target 'MultiplayerAI.exe'
    if (-not $NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $programs = [Environment]::GetFolderPath('Programs')
        $desktop = [Environment]::GetFolderPath('DesktopDirectory')
        foreach ($folder in @($programs, $desktop)) {
            if (-not $folder) { continue }
            $shortcut = $shell.CreateShortcut((Join-Path $folder 'Multiplayer AI.lnk'))
            $shortcut.TargetPath = $exe
            $shortcut.WorkingDirectory = $target
            $shortcut.IconLocation = "$exe,0"
            $shortcut.Description = 'Multiplayer AI desktop workspace'
            $shortcut.Save()
        }
    }
    Write-Host "Installed successfully: $exe"
    if (-not $NoLaunch) { Start-Process -FilePath $exe }
} catch {
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
