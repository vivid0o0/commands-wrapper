$ErrorActionPreference = "Stop"

$PrimaryWrapper = "commands-wrapper"
$ShortAlias = "cw"

$StepTotal = 9
$StepCurrent = 0
$MaxSourceArchiveBytes = 67108864
$MaxSourceExtractedBytes = 134217728
$MaxSourceMembers = 10000

function Invoke-RichUi {
    param(
        [string]$Mode,
        [string]$Message = "",
        [int]$Current = 0,
        [int]$Total = 0,
        [string]$Version = ""
    )

    switch ($Mode) {
        "logo" { Write-Host "commands-wrapper" -ForegroundColor Cyan }
        "step" { Write-Host "[$Current/$Total] $Message" -ForegroundColor Cyan }
        "ok" { Write-Host "Done" -ForegroundColor Green }
        "warn" { Write-Host "WARN: $Message" -ForegroundColor Yellow }
        "error" { Write-Host "ERROR: $Message" -ForegroundColor Red }
        "info" { Write-Host $Message -ForegroundColor Cyan }
        "detail" { Write-Host $Message -ForegroundColor DarkGray }
        "success-panel" {
            if ($Version) {
                Write-Host "commands-wrapper $Version is installed and self-healed." -ForegroundColor Green
            } else {
                Write-Host "commands-wrapper is installed and self-healed." -ForegroundColor Green
            }
            Write-Host "Use 'cw' or 'commands-wrapper' from any directory." -ForegroundColor Gray
        }
        default {
            if ($Message) {
                Write-Host $Message
            }
        }
    }
}

function Ensure-UiRuntime {
    return
}

function Test-AllowedSourceUrl {
    param([string]$Url)

    try {
        $uri = [System.Uri]$Url
    } catch {
        return $false
    }

    if ($uri.IsAbsoluteUri -and $uri.Scheme -eq "https") {
        return $true
    }

    $allowInsecure = $env:COMMANDS_WRAPPER_ALLOW_INSECURE_SOURCE -in @("1", "true", "yes", "on")
    if (-not $allowInsecure) {
        return $false
    }

    return $uri.IsAbsoluteUri -and $uri.Scheme -in @("http", "file")
}

function Receive-SourceArchiveSafely {
    param(
        [string]$Url,
        [string]$Destination,
        [long]$MaxBytes
    )

    $code = @'
import os
import pathlib
import sys
import urllib.parse
import urllib.request

url = sys.argv[1]
destination = pathlib.Path(sys.argv[2])
max_bytes = int(sys.argv[3])
allow_insecure = os.environ.get("COMMANDS_WRAPPER_ALLOW_INSECURE_SOURCE", "").strip().lower() in {
    "1", "true", "yes", "on"
}

def validate(candidate):
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if allow_insecure and (
        (parsed.scheme == "http" and parsed.netloc)
        or (parsed.scheme == "file" and parsed.path)
    ):
        return
    raise RuntimeError("source URL or redirect is not allowed")

validate(url)
with urllib.request.urlopen(url, timeout=30) as response:
    validate(response.geturl())
    declared = response.headers.get("Content-Length")
    if declared:
        try:
            declared_size = int(declared)
        except ValueError:
            declared_size = 0
        if declared_size > max_bytes:
            raise RuntimeError("source archive exceeds the compressed-size safety limit")

    downloaded = 0
    with destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            downloaded += len(chunk)
            if downloaded > max_bytes:
                raise RuntimeError("source archive exceeds the compressed-size safety limit")
            output.write(chunk)
'@

    Invoke-Python @("-c", $code, $Url, $Destination, [string]$MaxBytes)
}

function Expand-SourceArchiveSafely {
    param(
        [string]$ArchivePath,
        [string]$Destination,
        [long]$MaxExtractedBytes,
        [int]$MaxMembers
    )

    $code = @'
import os
import pathlib
import shutil
import stat
import sys
import tarfile

archive_path = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
max_bytes = int(sys.argv[3])
max_members = int(sys.argv[4])
destination.mkdir(parents=True, exist_ok=True)
destination_real = destination.resolve()

with tarfile.open(archive_path, mode="r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise RuntimeError("source archive is empty")
    if len(members) > max_members:
        raise RuntimeError("source archive contains too many entries")

    top_levels = set()
    total_size = 0
    validated = []
    for member in members:
        member_path = pathlib.PurePosixPath(member.name)
        parts = member_path.parts
        if member_path.is_absolute() or ".." in parts or not parts:
            raise RuntimeError(f"unsafe source archive path: {member.name}")
        top_levels.add(parts[0])
        if len(top_levels) != 1:
            raise RuntimeError("source archive must contain exactly one top-level directory")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise RuntimeError(f"unsupported source archive entry: {member.name}")
        if not (member.isdir() or member.isfile()):
            raise RuntimeError(f"unsupported source archive entry: {member.name}")

        relative_parts = parts[1:]
        if not relative_parts:
            continue
        target = destination.joinpath(*relative_parts)
        target_real = target.resolve(strict=False)
        if os.path.commonpath((str(destination_real), str(target_real))) != str(destination_real):
            raise RuntimeError(f"unsafe source archive path: {member.name}")

        if member.isfile():
            total_size += member.size
            if total_size > max_bytes:
                raise RuntimeError("source archive exceeds the extracted-size safety limit")
        validated.append((member, target))

    for member, target in validated:
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise RuntimeError(f"failed to read source archive entry: {member.name}")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        safe_mode = 0o755 if member.mode & stat.S_IXUSR else 0o644
        target.chmod(safe_mode)
'@

    Invoke-Python @(
        "-c",
        $code,
        $ArchivePath,
        $Destination,
        [string]$MaxExtractedBytes,
        [string]$MaxMembers
    )
}

function Write-Logo {
    Invoke-RichUi -Mode "logo"
}

function Start-Step {
    param([string]$Message)

    $script:StepCurrent += 1
    Invoke-RichUi -Mode "step" -Message $Message -Current $script:StepCurrent -Total $script:StepTotal
}

function Complete-Step {
    Invoke-RichUi -Mode "ok"
}

function Warn-Step {
    param([string]$Message)
    Invoke-RichUi -Mode "warn" -Message $Message
}

function Fail-Install {
    param([string]$Message)
    Invoke-RichUi -Mode "error" -Message $Message
    throw $Message
}

function Invoke-Python {
    param([string[]]$PythonArgs)

    $pyExitCode = $null
    $pythonExitCode = $null
    $errors = @()

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @PythonArgs
        $pyExitCode = $LASTEXITCODE
        if ($pyExitCode -eq 0) {
            return
        }
        $errors += "'py -3' failed with exit code $pyExitCode"
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python @PythonArgs
        $pythonExitCode = $LASTEXITCODE
        if ($pythonExitCode -eq 0) {
            return
        }
        $errors += "'python' failed with exit code $pythonExitCode"
    }

    if ($errors.Count -gt 0) {
        throw ($errors -join "; ")
    }

    throw "Python 3 was not found in PATH."
}

function Invoke-PythonCapture {
    param([string[]]$PythonArgs)

    if (Get-Command py -ErrorAction SilentlyContinue) {
        $output = & py -3 @PythonArgs 2>$null
        if ($LASTEXITCODE -eq 0) {
            $lastLine = $output | Select-Object -Last 1
            if ($null -ne $lastLine) {
                $trimmed = $lastLine.ToString().Trim()
                if ($trimmed) {
                    return $trimmed
                }
            }
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        $output = & python @PythonArgs 2>$null
        if ($LASTEXITCODE -eq 0) {
            $lastLine = $output | Select-Object -Last 1
            if ($null -ne $lastLine) {
                $trimmed = $lastLine.ToString().Trim()
                if ($trimmed) {
                    return $trimmed
                }
            }
        }
    }

    return $null
}

function Get-PipInstallScope {
    $scope = Invoke-PythonCapture @(
        "-c",
        "import sys; print('venv' if getattr(sys, 'base_prefix', sys.prefix) != sys.prefix else 'user')"
    )
    if ($scope -eq "user") {
        return ,"--user"
    }
    return @()
}

function Test-EnvironmentFlag {
    param([string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name)
    return $value -in @("1", "true", "TRUE", "yes", "YES", "on", "ON")
}

function Get-PythonScriptsDir {
    $code = @'
import os
import site
import sys
import sysconfig

in_venv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix
scripts = None
if in_venv:
    scripts = sysconfig.get_path("scripts")
else:
    scheme = f"{os.name}_user"
    if scheme in sysconfig.get_scheme_names():
        scripts = sysconfig.get_path("scripts", scheme=scheme)

if not scripts:
    scripts = os.path.join(site.USER_BASE or os.path.expanduser("~"), "bin")

print(os.path.abspath(scripts))
'@

    return Invoke-PythonCapture @("-c", $code)
}

function Get-UserConfigDir {
    $code = @'
import os

if os.name == "nt":
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    print(os.path.join(base, "commands-wrapper"))
else:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        print(os.path.join(os.path.expanduser(xdg), "commands-wrapper"))
    else:
        print(os.path.join(os.path.expanduser("~"), ".config", "commands-wrapper"))
'@

    return Invoke-PythonCapture @("-c", $code)
}

function Get-InstalledPackageVersion {
    $code = @'
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "pip", "show", "commands-wrapper"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
)
if result.returncode != 0:
    raise SystemExit(0)

for line in result.stdout.splitlines():
    if line.startswith("Version:"):
        print(line.split(":", 1)[1].strip())
        break
'@

    $version = Invoke-PythonCapture @("-c", $code)
    if ($version -and $version -match '^[0-9][0-9A-Za-z.!+_-]*$') {
        return $version
    }
    return $null
}

function Get-ProjectVersionFromPyproject {
    param([string]$RootPath)

    if (-not $RootPath) {
        return $null
    }

    $pyprojectPath = Join-Path $RootPath "pyproject.toml"
    if (-not (Test-Path $pyprojectPath)) {
        return $null
    }

    $code = @'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(0)

text = path.read_text(encoding="utf-8", errors="replace")
version = None

try:
    import tomllib
except Exception:
    tomllib = None

if tomllib is not None:
    try:
        data = tomllib.loads(text)
    except Exception:
        data = {}
    project = data.get("project") if isinstance(data, dict) else None
    if isinstance(project, dict):
        value = project.get("version")
        if isinstance(value, str) and value.strip():
            version = value.strip()

if version is None:
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if not in_project:
            continue
        match = re.match(r'^version\s*=\s*["\']([^"\']+)["\']\s*$', stripped)
        if match:
            version = match.group(1).strip()
            break

if version:
    print(version)
'@

    return Invoke-PythonCapture @("-c", $code, $pyprojectPath)
}

function Compare-PackageVersion {
    param(
        [string]$InstalledVersion,
        [string]$TargetVersion
    )

    if (-not $InstalledVersion -or -not $TargetVersion) {
        return "unknown"
    }

    $code = @'
import sys

installed = sys.argv[1]
target = sys.argv[2]

try:
    from packaging.version import Version
    installed_v = Version(installed)
    target_v = Version(target)
except Exception:
    if installed == target:
        print("eq")
    else:
        print("unknown")
    raise SystemExit(0)

if installed_v == target_v:
    print("eq")
elif installed_v > target_v:
    print("gt")
else:
    print("lt")
'@

    $result = Invoke-PythonCapture @("-c", $code, $InstalledVersion, $TargetVersion)
    if (-not $result) {
        return "unknown"
    }
    return $result
}

function Resolve-WrapperSyncCommand {
    param([string]$ScriptsDir)

    if (-not $ScriptsDir) {
        return $null
    }

    $candidates = @(
        "commands-wrapper.exe",
        "commands-wrapper",
        "commands-wrapper.cmd",
        "commands-wrapper.ps1"
    )

    foreach ($candidate in $candidates) {
        $path = Join-Path $ScriptsDir $candidate
        if (Test-Path $path) {
            return $path
        }
    }

    return $null
}

function Test-CommandsWrapperSourceRoot {
    param([string]$Root)

    if (-not $Root) {
        return $false
    }

    $pyproject = Join-Path $Root "pyproject.toml"
    $cliPath = Join-Path (Join-Path $Root ".commands-wrapper") "commands-wrapper"
    return (Test-Path $pyproject) -and (Test-Path $cliPath)
}

function Normalize-PathSafe {
    param([string]$PathValue)

    if (-not $PathValue -or $PathValue.Trim() -eq "") {
        return $null
    }

    try {
        return [System.IO.Path]::GetFullPath($PathValue)
    } catch {
        return $null
    }
}

function Ensure-UserPathContains {
    param([string]$PathEntry)

    if (-not $PathEntry) {
        return
    }

    $normalizedTarget = Normalize-PathSafe -PathValue $PathEntry
    if (-not $normalizedTarget) {
        Warn-Step "Could not normalize scripts directory path '$PathEntry'; skipping persistent PATH update."
        return
    }

    $userPath = ""
    $userPathSupported = $true
    try {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    } catch {
        $userPathSupported = $false
    }
    $segments = @()
    if ($userPath) {
        $segments = $userPath.Split(";") | Where-Object { $_ -and $_.Trim() -ne "" }
    }

    $contains = $false
    foreach ($segment in $segments) {
        $normalizedSegment = Normalize-PathSafe -PathValue $segment
        if (-not $normalizedSegment) {
            continue
        }
        if ([string]::Equals($normalizedSegment, $normalizedTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
            $contains = $true
            break
        }
    }

    if ($userPathSupported -and -not $contains) {
        $updatedSegments = @($normalizedTarget) + $segments
        try {
            [Environment]::SetEnvironmentVariable("Path", ($updatedSegments -join ";"), "User")
        } catch {
            $userPathSupported = $false
        }
    }

    $sessionSeparator = [System.IO.Path]::PathSeparator
    $sessionSegments = @()
    if ($env:PATH) {
        $sessionSegments = $env:PATH.Split($sessionSeparator) | Where-Object { $_ -and $_.Trim() -ne "" }
    }
    $sessionContains = $false
    foreach ($segment in $sessionSegments) {
        $normalizedSegment = Normalize-PathSafe -PathValue $segment
        if (-not $normalizedSegment) {
            continue
        }
        if ([string]::Equals($normalizedSegment, $normalizedTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
            $sessionContains = $true
            break
        }
    }
    if (-not $sessionContains) {
        $env:PATH = "$normalizedTarget$sessionSeparator$env:PATH"
    }

    if (-not $userPathSupported) {
        Warn-Step "Could not persist user PATH permanently on this host; session PATH was repaired."
    }
}

function Invoke-WrapperSyncWithRetry {
    param([string]$WrapperCommand)

    $firstSyncOutput = & $WrapperCommand sync 2>&1
    $firstExitCode = $LASTEXITCODE
    if ($firstExitCode -eq 0) {
        return
    }

    Warn-Step "Initial wrapper sync failed; retrying with diagnostics."
    $secondSyncOutput = & $WrapperCommand sync 2>&1
    $secondExitCode = $LASTEXITCODE
    if ($secondExitCode -eq 0) {
        return
    }

    if ($firstSyncOutput) {
        Write-Host "Sync attempt output:" -ForegroundColor Red
        $firstSyncOutput | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    }
    if ($secondSyncOutput) {
        Write-Host "Retry sync output:" -ForegroundColor Red
        $secondSyncOutput | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    }
    throw "automatic wrapper sync failed after retry"
}

function Assert-CommandAvailable {
    param(
        [string]$Name,
        [string]$ExpectedDir
    )

    $commandInfo = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $commandInfo) {
        throw "global access check failed: '$Name' is not discoverable in PATH"
    }

    if (-not $ExpectedDir) {
        return
    }

    $normalizedExpectedDir = Normalize-PathSafe -PathValue $ExpectedDir
    if (-not $normalizedExpectedDir) {
        return
    }

    $commandPath = $commandInfo.Source
    if (-not $commandPath) {
        $commandPath = $commandInfo.Path
    }
    $normalizedCommandPath = Normalize-PathSafe -PathValue $commandPath
    if (-not $normalizedCommandPath) {
        throw "global access check failed: '$Name' resolves to a non-file command."
    }

    $commandDir = Split-Path -Parent $normalizedCommandPath
    if (-not [string]::Equals($commandDir, $normalizedExpectedDir, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "global access check failed: '$Name' resolves to '$normalizedCommandPath' instead of '$normalizedExpectedDir'."
    }
}

function Ensure-GlobalConfigTemplate {
    $configDir = Get-UserConfigDir
    if (-not $configDir) {
        throw "failed to determine global config directory"
    }

    New-Item -ItemType Directory -Force -Path $configDir | Out-Null

    $yaml = Join-Path $configDir "commands.yaml"
    $yml = Join-Path $configDir "commands.yml"
    if (-not (Test-Path $yaml) -and -not (Test-Path $yml)) {
@'
# command-name:
#   description: "What this command does"
#   steps 60:
#     - command: "shell command here"
#     - send: "text to type into process"
#     - press_key: "enter"
#     - wait: "2"
'@ | Out-File -Encoding utf8 $yaml
    }
}

Ensure-UiRuntime
Write-Logo
Invoke-RichUi -Mode "info" -Message "Installing commands-wrapper"

$repoRoot = $null
if ($MyInvocation.MyCommand.Path) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    if ($scriptDir) {
        $repoRoot = Split-Path -Parent $scriptDir
    }
}

$cwdRoot = (Get-Location).Path
$repoSourceRoot = $null
if (Test-CommandsWrapperSourceRoot -Root $repoRoot) {
    $repoSourceRoot = $repoRoot
}

$cwdSourceRoot = $null
if (Test-CommandsWrapperSourceRoot -Root $cwdRoot) {
    $cwdSourceRoot = $cwdRoot
}

$sourceUrl = if ($env:COMMANDS_WRAPPER_SOURCE_URL) {
    $env:COMMANDS_WRAPPER_SOURCE_URL
} else {
    ""
}

$sourceSha256 = if ($env:COMMANDS_WRAPPER_SOURCE_SHA256) {
    $env:COMMANDS_WRAPPER_SOURCE_SHA256.Trim().ToLowerInvariant()
} else {
    ""
}

if ($sourceUrl -and -not (Test-AllowedSourceUrl -Url $sourceUrl)) {
    Fail-Install "source URL must use HTTPS; set COMMANDS_WRAPPER_ALLOW_INSECURE_SOURCE=1 only for a trusted development source"
}

if ($sourceSha256 -and -not $sourceUrl) {
    Fail-Install "COMMANDS_WRAPPER_SOURCE_SHA256 requires COMMANDS_WRAPPER_SOURCE_URL"
}

if ($sourceSha256 -and $sourceSha256 -notmatch '^[0-9a-f]{64}$') {
    Fail-Install "invalid COMMANDS_WRAPPER_SOURCE_SHA256 value"
}

Start-Step "Checking Python and pip availability"
if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail-Install "Python 3 was not found in PATH."
}
Invoke-Python @("-m", "pip", "--version")
Complete-Step

Start-Step "Preparing installation source"
$tempRoot = $null
$tempArchive = $null
$installTargetRoot = $null
if ($repoSourceRoot) {
    $installTarget = $repoSourceRoot
    $installTargetRoot = $repoSourceRoot
} elseif ($cwdSourceRoot) {
    $installTarget = $cwdSourceRoot
    $installTargetRoot = $cwdSourceRoot
} elseif ($sourceUrl) {
    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("commands-wrapper-" + [guid]::NewGuid().ToString())
    $tempArchive = Join-Path $tempRoot "commands-wrapper.tar.gz"
    $extractDir = Join-Path $tempRoot "source"
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    try {
        Receive-SourceArchiveSafely -Url $sourceUrl -Destination $tempArchive -MaxBytes $MaxSourceArchiveBytes
        if ($sourceSha256) {
            $actualSha256 = (Get-FileHash -Path $tempArchive -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($actualSha256 -ne $sourceSha256) {
                Fail-Install "source archive checksum mismatch"
            }
        }
        Expand-SourceArchiveSafely `
            -ArchivePath $tempArchive `
            -Destination $extractDir `
            -MaxExtractedBytes $MaxSourceExtractedBytes `
            -MaxMembers $MaxSourceMembers
    } catch {
        if ($tempRoot -and (Test-Path $tempRoot)) {
            Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
        }
        Fail-Install "source archive failed security validation, download, or extraction: $($_.Exception.Message)"
    }
    if (-not (Test-CommandsWrapperSourceRoot -Root $extractDir)) {
        if ($tempRoot -and (Test-Path $tempRoot)) {
            Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
        }
        Fail-Install "downloaded archive does not contain a valid commands-wrapper source tree"
    }
    $installTarget = $extractDir
    $installTargetRoot = $extractDir
} else {
    $installTarget = $PrimaryWrapper
}
Complete-Step

Start-Step "Installing/updating package"
try {
    $installedVersion = Get-InstalledPackageVersion
    $targetVersion = Get-ProjectVersionFromPyproject -RootPath $installTargetRoot
    $relation = Compare-PackageVersion -InstalledVersion $installedVersion -TargetVersion $targetVersion
    if ($relation -eq "eq") {
        Warn-Step "commands-wrapper $installedVersion is already installed; skipping package reinstall."
    } elseif ($relation -eq "gt") {
        Warn-Step "installed version $installedVersion is newer than source $targetVersion; skipping downgrade."
    } else {
        $pipScope = @(Get-PipInstallScope)
        $installArgs = @("-m", "pip", "install", "--upgrade") + $pipScope + @($installTarget)
        try {
            Invoke-Python $installArgs
        } catch {
            if (-not (Test-EnvironmentFlag -Name "COMMANDS_WRAPPER_ALLOW_BREAK_SYSTEM_PACKAGES")) {
                throw
            }
            Warn-Step "Standard pip install failed; retrying with --break-system-packages because explicit opt-in is enabled."
            Invoke-Python ($installArgs + @("--break-system-packages"))
        }
    }
    Complete-Step
} finally {
    if ($tempRoot -and (Test-Path $tempRoot)) {
        Remove-Item -Recurse -Force $tempRoot -ErrorAction SilentlyContinue
    }
}

Start-Step "Resolving command locations"
$scriptsDir = Get-PythonScriptsDir
if (-not $scriptsDir) {
    Fail-Install "failed to resolve Python scripts directory"
}

$syncCommand = Resolve-WrapperSyncCommand -ScriptsDir $scriptsDir
if (-not $syncCommand) {
    Fail-Install "installed binary was not found in '$scriptsDir'"
}
Complete-Step

Start-Step "Synchronizing wrapper commands"
try {
    Invoke-WrapperSyncWithRetry -WrapperCommand $syncCommand
} catch {
    Fail-Install $_.Exception.Message
}

$cwCandidates = @(
    (Join-Path $scriptsDir "cw.cmd"),
    (Join-Path $scriptsDir "cw.ps1"),
    (Join-Path $scriptsDir "cw.exe"),
    (Join-Path $scriptsDir "cw")
)
if (-not ($cwCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1)) {
    Fail-Install "wrapper sync completed but no '$ShortAlias' wrapper was generated in '$scriptsDir'"
}
Complete-Step

Start-Step "Self-healing PATH for global command access"
Ensure-UserPathContains -PathEntry $scriptsDir
Assert-CommandAvailable -Name $PrimaryWrapper -ExpectedDir $scriptsDir
Assert-CommandAvailable -Name $ShortAlias -ExpectedDir $scriptsDir
Complete-Step

Start-Step "Ensuring global command config exists"
Ensure-GlobalConfigTemplate
Complete-Step

Start-Step "Running post-install launch preview"
$isInteractive = $false
try {
    $isInteractive = -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected
} catch {
    $isInteractive = $false
}

if ($isInteractive -and -not $env:CI) {
    & $syncCommand
    if ($LASTEXITCODE -ne 0) {
        Fail-Install "post-install launch preview failed with exit code $LASTEXITCODE"
    }
} else {
    & $syncCommand list | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail-Install "post-install verification failed: 'commands-wrapper list' exited with code $LASTEXITCODE"
    }
}
Complete-Step

Start-Step "Final health check"
& $syncCommand --help | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fail-Install "final health check failed: 'commands-wrapper --help' exited with code $LASTEXITCODE"
}
Complete-Step

$installedAfter = Get-InstalledPackageVersion
Invoke-RichUi -Mode "success-panel" -Version $installedAfter
