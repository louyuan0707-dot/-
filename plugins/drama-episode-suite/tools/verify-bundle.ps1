[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = Split-Path -Parent $PSScriptRoot
$rootFull = [IO.Path]::GetFullPath($root).TrimEnd('\') + '\'
$manifestPath = Join-Path $root '.codex-plugin\plugin.json'
$hashPath = Join-Path $root 'checksums.sha256'

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Plugin manifest is missing.' }
if (-not (Test-Path -LiteralPath $hashPath -PathType Leaf)) { throw 'Checksum list is missing.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.name -ne 'drama-episode-suite' -or $manifest.skills -ne './skills/') { throw 'Unexpected plugin manifest identity or skills path.' }

$skillNames = @('drama-episode-controller', 'lyy', 'jianying-rough-cut')
foreach ($name in $skillNames) {
    $skill = Join-Path $root "skills\$name\SKILL.md"
    if (-not (Test-Path -LiteralPath $skill -PathType Leaf)) { throw "Missing Skill: $name" }
}

$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($line in Get-Content -LiteralPath $hashPath -Encoding UTF8) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    if ($line -notmatch '^([0-9A-Fa-f]{64})\s+\*(.+)$') { throw "Malformed checksum line: $line" }
    $expected = $Matches[1].ToUpperInvariant()
    $relative = $Matches[2].Replace('/', '\')
    $full = [IO.Path]::GetFullPath((Join-Path $root $relative))
    if (-not $full.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) { throw "Checksum path escapes bundle: $relative" }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "Missing bundled file: $relative" }
    if (-not $seen.Add($relative)) { throw "Duplicate checksum entry: $relative" }
    $actual = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash
    if ($actual -ne $expected) { throw "SHA-256 mismatch: $relative" }
}

$files = @(Get-ChildItem -LiteralPath $root -File -Recurse | Where-Object { $_.FullName -ne $hashPath })
if ($seen.Count -ne $files.Count) { throw "Checksum coverage mismatch: listed $($seen.Count), files $($files.Count)" }
Write-Host "Verified $($seen.Count) files and 3 Skill entrypoints."
