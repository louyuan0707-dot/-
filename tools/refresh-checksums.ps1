[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repoRoot = Split-Path -Parent $PSScriptRoot
$pluginRoot = Join-Path $repoRoot 'plugins\drama-episode-suite'
$checksumPath = Join-Path $pluginRoot 'checksums.sha256'
if (-not (Test-Path -LiteralPath (Join-Path $pluginRoot '.codex-plugin\plugin.json') -PathType Leaf)) {
    throw 'Plugin manifest is missing.'
}
$files = @(Get-ChildItem -LiteralPath $pluginRoot -File -Recurse |
    Where-Object { $_.FullName -ne $checksumPath } |
    Sort-Object FullName)
$lines = foreach ($file in $files) {
    $relative = $file.FullName.Substring($pluginRoot.Length + 1).Replace('\', '/')
    $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash *$relative"
}
[IO.File]::WriteAllLines($checksumPath, $lines, [Text.UTF8Encoding]::new($false))
& (Join-Path $pluginRoot 'tools\verify-bundle.ps1')
Write-Host "Updated checksums for $($files.Count) plugin files."
