[CmdletBinding()]
param([string]$PythonExe, [switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$BundleRoot = Split-Path -Parent $PSScriptRoot
$BundledPlugin = $BundleRoot
$Timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $env:USERPROFILE) { throw 'USERPROFILE is empty.' }
function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("Command failed with exit code {0}: {1} {2}" -f $LASTEXITCODE, $FilePath, ($Arguments -join ' '))
    }
}

function Resolve-BasePython {
    param([string]$RequestedPython)

    $Candidates = New-Object System.Collections.Generic.List[string]
    if ($RequestedPython) {
        $Candidates.Add($RequestedPython)
    }

    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $PyLauncher) {
        try {
            $Resolved = (& $PyLauncher.Source -3.11 -c "import sys; print(sys.executable)" 2>$null | Select-Object -Last 1)
            if ($Resolved) {
                $Candidates.Add($Resolved.Trim())
            }
        } catch {
        }
    }

    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $PythonCommand) {
        $Candidates.Add($PythonCommand.Source)
    }

    $KnownPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    if (Test-Path -LiteralPath $KnownPython -PathType Leaf) {
        $Candidates.Add($KnownPython)
    }

    foreach ($Candidate in $Candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            continue
        }
        try {
            $VersionText = (& $Candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Select-Object -Last 1)
            if ($VersionText -match '^3\.(10|11|12)$') {
                return (Resolve-Path -LiteralPath $Candidate).Path
            }
        } catch {
        }
    }

    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -eq $Winget) {
        throw 'Python 3.10-3.12 was not found. Install 64-bit Python 3.11, then rerun install.ps1.'
    }

    Write-Host 'Python 3.11 was not found. Installing it with winget...'
    Invoke-Checked -FilePath $Winget.Source -Arguments @(
        'install', '--id', 'Python.Python.3.11', '--exact', '--scope', 'user',
        '--accept-package-agreements', '--accept-source-agreements',
        '--disable-interactivity'
    )

    if (Test-Path -LiteralPath $KnownPython -PathType Leaf) {
        return (Resolve-Path -LiteralPath $KnownPython).Path
    }

    $InstalledPython = Get-ChildItem -LiteralPath (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter python.exe -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match 'Python31(0|1|2)' } |
        Select-Object -First 1
    if ($null -eq $InstalledPython) {
        throw 'Python installation completed, but python.exe could not be located. Reopen PowerShell and rerun install.ps1.'
    }
    return $InstalledPython.FullName
}

function Install-PortableRuntime {
    param([string]$RequestedPython)

    $CodexRoot = Join-Path $env:USERPROFILE '.codex'
    $VenvRoot = Join-Path $CodexRoot 'envs\jianying-rough-cut'
    $VenvPython = Join-Path $VenvRoot 'Scripts\python.exe'
    $Requirements = Join-Path $BundledPlugin 'skills\jianying-rough-cut\requirements.txt'

    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        $BasePython = Resolve-BasePython -RequestedPython $RequestedPython
        Write-Host "Creating dedicated Python environment: $VenvRoot"
        Invoke-Checked -FilePath $BasePython -Arguments @('-m', 'venv', $VenvRoot)
    } else {
        Write-Host "Reusing dedicated Python environment: $VenvRoot"
    }

    Write-Host 'Installing Python dependencies...'
    Invoke-Checked -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade', 'pip')
    Invoke-Checked -FilePath $VenvPython -Arguments @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', $Requirements)

    $FfmpegRoot = Join-Path $CodexRoot 'tools\ffmpeg-9.0\ffmpeg-9.0-full_build'
    $FfmpegBin = Join-Path $FfmpegRoot 'bin'
    $FfmpegExe = Join-Path $FfmpegBin 'ffmpeg.exe'
    $FfprobeExe = Join-Path $FfmpegBin 'ffprobe.exe'

    if ((Test-Path -LiteralPath $FfmpegExe -PathType Leaf) -and (Test-Path -LiteralPath $FfprobeExe -PathType Leaf)) {
        Write-Host "Reusing FFmpeg: $FfmpegBin"
        return
    }

    $TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("jianying-rough-cut-" + [guid]::NewGuid().ToString('N'))
    $ZipPath = Join-Path $TempRoot 'ffmpeg-release-essentials.zip'
    $HashPath = Join-Path $TempRoot 'ffmpeg-release-essentials.zip.sha256'
    $ExtractRoot = Join-Path $TempRoot 'expanded'
    New-Item -ItemType Directory -Path $ExtractRoot -Force | Out-Null

    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $FfmpegUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        $FfmpegHashUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256'
        Write-Host 'Downloading the FFmpeg release essentials build (about 106 MB)...'
        Invoke-WebRequest -UseBasicParsing -Uri $FfmpegUrl -OutFile $ZipPath
        Invoke-WebRequest -UseBasicParsing -Uri $FfmpegHashUrl -OutFile $HashPath

        $HashText = (Get-Content -LiteralPath $HashPath -Raw).Trim()
        if ($HashText -notmatch '([A-Fa-f0-9]{64})') {
            throw 'Could not parse the published FFmpeg SHA-256 checksum.'
        }
        $ExpectedFfmpegHash = $Matches[1].ToUpperInvariant()
        $ActualFfmpegHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
        if ($ActualFfmpegHash -ne $ExpectedFfmpegHash) {
            throw 'Downloaded FFmpeg ZIP failed SHA-256 verification.'
        }

        Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractRoot -Force
        $FfmpegCandidate = Get-ChildItem -LiteralPath $ExtractRoot -Filter ffmpeg.exe -Recurse -File |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName 'ffprobe.exe') -PathType Leaf } |
            Select-Object -First 1
        if ($null -eq $FfmpegCandidate) {
            throw 'The FFmpeg archive does not contain ffmpeg.exe and ffprobe.exe in one bin directory.'
        }

        $BuildRoot = Split-Path -Parent $FfmpegCandidate.DirectoryName
        if (Test-Path -LiteralPath $FfmpegRoot) {
            $BackupRoot = "$FfmpegRoot.backup.$Timestamp"
            Move-Item -LiteralPath $FfmpegRoot -Destination $BackupRoot
            Write-Host "Incomplete FFmpeg folder backed up: $BackupRoot"
        }
        New-Item -ItemType Directory -Path $FfmpegRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $BuildRoot '*') -Destination $FfmpegRoot -Recurse -Force

        if (-not ((Test-Path -LiteralPath $FfmpegExe -PathType Leaf) -and (Test-Path -LiteralPath $FfprobeExe -PathType Leaf))) {
            throw 'FFmpeg extraction completed, but the expected executables are missing.'
        }
        Write-Host "FFmpeg installed: $FfmpegBin"
    } finally {
        $ResolvedTemp = [System.IO.Path]::GetFullPath($TempRoot)
        $SystemTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if ($ResolvedTemp.StartsWith($SystemTemp, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $ResolvedTemp)) {
            Remove-Item -LiteralPath $ResolvedTemp -Recurse -Force
        }
    }
}

$venvPython = Join-Path $env:USERPROFILE '.codex\envs\jianying-rough-cut\Scripts\python.exe'
$ffmpeg = Join-Path $env:USERPROFILE '.codex\tools\ffmpeg-9.0\ffmpeg-9.0-full_build\bin\ffmpeg.exe'
$ffprobe = Join-Path $env:USERPROFILE '.codex\tools\ffmpeg-9.0\ffmpeg-9.0-full_build\bin\ffprobe.exe'
if ($CheckOnly) {
    $allPresent = (Test-Path -LiteralPath $venvPython -PathType Leaf) -and (Test-Path -LiteralPath $ffmpeg -PathType Leaf) -and (Test-Path -LiteralPath $ffprobe -PathType Leaf)
    Write-Output "Jianying runtime present: $allPresent"
    if (-not $allPresent) { exit 2 }
    exit 0
}
Install-PortableRuntime -RequestedPython $PythonExe
& $venvPython -m compileall -q (Join-Path $BundledPlugin 'skills\jianying-rough-cut\scripts')
if ($LASTEXITCODE -ne 0) { throw 'Jianying script compilation failed.' }
Write-Host 'Jianying runtime installed. Restart Codex before using the combined plugin.'