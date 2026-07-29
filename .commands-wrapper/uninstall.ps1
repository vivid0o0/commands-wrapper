$ErrorActionPreference = "Stop"

function Invoke-Python {
    param([string[]]$PythonArgs)

    $pyExitCode = $null
    $pythonExitCode = $null

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @PythonArgs
        $pyExitCode = $LASTEXITCODE
        if ($pyExitCode -eq 0) {
            return
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python @PythonArgs
        $pythonExitCode = $LASTEXITCODE
        if ($pythonExitCode -eq 0) {
            return
        }
    }

    if ($pyExitCode -ne $null -and $pythonExitCode -ne $null) {
        throw "Both 'py -3' (exit $pyExitCode) and 'python' (exit $pythonExitCode) failed."
    }
    if ($pyExitCode -ne $null) {
        throw "'py -3' failed with exit code $pyExitCode."
    }
    if ($pythonExitCode -ne $null) {
        throw "'python' failed with exit code $pythonExitCode."
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

function Test-PackageInstalled {
    $candidates = @(
        @{ exe = "py"; args = @("-3"); label = "py -3" },
        @{ exe = "python"; args = @(); label = "python" }
    )
    $sawInterpreter = $false
    $sawNotInstalled = $false

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.exe -ErrorAction SilentlyContinue)) {
            continue
        }
        $sawInterpreter = $true

        $commandArgs = @($candidate.args) + @("-m", "pip", "show", "commands-wrapper")
        & $candidate.exe @commandArgs | Out-Null
        $showExitCode = $LASTEXITCODE
        if ($showExitCode -eq 0) {
            return $true
        }
        if ($showExitCode -eq 1) {
            $sawNotInstalled = $true
            continue
        }

        throw "'$($candidate.label) -m pip show commands-wrapper' failed with exit code $showExitCode."
    }

    if ($sawInterpreter -and $sawNotInstalled) {
        return $false
    }

    throw "Python 3 was not found in PATH."
}

function Confirm-Action {
    param([string]$Prompt)

    if ($env:COMMANDS_WRAPPER_UNINSTALL_FORCE -eq "1") {
        return $true
    }

    $isInteractive = $false
    try {
        $isInteractive = -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected
    } catch {
        $isInteractive = $false
    }

    if (-not $isInteractive) {
        return $false
    }

    $answer = Read-Host "$Prompt [y/N]"
    if (-not $answer) {
        return $false
    }
    return $answer.Trim() -match '^(?i:y|yes)$'
}

function Remove-UserPathEntry {
    param([string]$PathEntry)

    $normalizedTarget = Normalize-PathSafe -PathValue $PathEntry
    if (-not $normalizedTarget) {
        return
    }

    try {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($userPath) {
            $segments = $userPath.Split(";") | Where-Object { $_ -and $_.Trim() -ne "" }
            $keptSegments = @()
            foreach ($segment in $segments) {
                $normalizedSegment = Normalize-PathSafe -PathValue $segment
                if (-not $normalizedSegment) {
                    continue
                }
                if (-not [string]::Equals($normalizedSegment, $normalizedTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $keptSegments += $segment
                }
            }
            [Environment]::SetEnvironmentVariable("Path", ($keptSegments -join ";"), "User")
        }
    } catch {
        Write-Host "WARN: Unable to update persistent user PATH on this host." -ForegroundColor Yellow
    }

    if ($env:PATH) {
        $sessionSeparator = [System.IO.Path]::PathSeparator
        $sessionSegments = $env:PATH.Split($sessionSeparator) | Where-Object { $_ -and $_.Trim() -ne "" }
        $keptSessionSegments = @()
        foreach ($segment in $sessionSegments) {
            $normalizedSegment = Normalize-PathSafe -PathValue $segment
            if (-not $normalizedSegment) {
                continue
            }
            if (-not [string]::Equals($normalizedSegment, $normalizedTarget, [System.StringComparison]::OrdinalIgnoreCase)) {
                $keptSessionSegments += $segment
            }
        }
        $env:PATH = ($keptSessionSegments -join $sessionSeparator)
    }
}

if (-not (Confirm-Action -Prompt "Continue and uninstall commands-wrapper?")) {
    Write-Host "Uninstall cancelled." -ForegroundColor Yellow
    Write-Host "Set COMMANDS_WRAPPER_UNINSTALL_FORCE=1 for non-interactive uninstall." -ForegroundColor Gray
    exit 0
}

$syncWarning = "Wrapper cleanup failed; continuing package uninstall."
$scriptsDir = Get-PythonScriptsDir
$syncCommand = Resolve-WrapperSyncCommand -ScriptsDir $scriptsDir

if ($syncCommand) {
    try {
        & $syncCommand sync --uninstall | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host $syncWarning -ForegroundColor Yellow
        }
    } catch {
        Write-Host $syncWarning -ForegroundColor Yellow
    }
}

$isInstalled = Test-PackageInstalled
if (-not $isInstalled) {
    Write-Host "commands-wrapper is not installed." -ForegroundColor Gray
} else {
    Invoke-Python @("-m", "pip", "uninstall", "commands-wrapper", "-y")
}

if ($scriptsDir) {
    Remove-UserPathEntry -PathEntry $scriptsDir
}

$configDir = Get-UserConfigDir
if ($configDir -and (Test-Path $configDir)) {
    if ($env:COMMANDS_WRAPPER_REMOVE_CONFIG -eq "1" -or (Confirm-Action -Prompt "Remove user config at '$configDir'?")) {
        Remove-Item -Recurse -Force $configDir
        Write-Host "Removed user config directory." -ForegroundColor Green
    } else {
        Write-Host "Kept user config at '$configDir'." -ForegroundColor Gray
    }
}

Write-Host "commands-wrapper uninstalled." -ForegroundColor Green
