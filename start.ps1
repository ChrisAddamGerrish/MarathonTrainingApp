<#
.SYNOPSIS
  Starts the Marathon Training app and opens it in your browser.

.DESCRIPTION
  1. Makes sure the Python environment (.venv) exists and has the app's dependencies.
  2. Makes sure the React front end is built (installs npm packages / rebuilds when the
     source is newer than the last build).
  3. Runs the FastAPI server, waits until it answers, then opens the browser.
  Press Ctrl+C in this window to stop it.

  Written for Windows PowerShell 5.1 and PowerShell 7. Double-click start.bat if scripts
  are blocked by your execution policy.

.PARAMETER Port
  Port for the app. Default 8000.

.PARAMETER NoBrowser
  Don't open the browser.

.PARAMETER Dev
  Development mode: server auto-reloads on Python changes, and the Vite dev server runs
  (hot reload for the React code) at http://localhost:5173.

.PARAMETER Rebuild
  Force a fresh front end build.

.EXAMPLE
  .\start.ps1
.EXAMPLE
  .\start.ps1 -Dev
.EXAMPLE
  .\start.ps1 -Port 8100 -NoBrowser
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$Dev,
    [switch]$Rebuild
)

$ErrorActionPreference = 'Stop'

# Everything runs inside try/catch so a failure prints one clean message instead of a stack trace.
try {
    $Root = $PSScriptRoot
    Set-Location -LiteralPath $Root

    $Frontend = Join-Path $Root 'frontend'
    $DistIndex = Join-Path $Frontend 'dist\index.html'
    $Python = Join-Path $Root '.venv\Scripts\python.exe'
    $Url = "http://localhost:$Port"

    function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }

    # Run a native command, fail with a clear message if it exits non-zero.
    function Invoke-Native([string]$What, [scriptblock]$Command) {
        & $Command
        if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
    }

    # True when something answers with this app's data on the port.
    function Test-MarathonUp([int]$P) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://127.0.0.1:$P/api/data"
            return ($r.StatusCode -eq 200 -and $r.Content -like '*plan_start*')
        } catch {
            return $false
        }
    }

    # Kill a process and everything it started (npm.cmd -> node).
    function Stop-Tree($Proc) {
        if ($Proc -and -not $Proc.HasExited) {
            & taskkill.exe /PID $Proc.Id /T /F 2>&1 | Out-Null
        }
    }

    # --- Already running? ----------------------------------------------------------------
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        if (Test-MarathonUp $Port) {
            Write-Host "Marathon is already running at $Url" -ForegroundColor Green
            if (-not $NoBrowser) { Start-Process $Url }
            exit 0
        }
        $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        $name = if ($owner) { $owner.ProcessName } else { 'another program' }
        throw "Port $Port is in use by $name (PID $($listener.OwningProcess)). Stop it, or run: .\start.ps1 -Port <other port>"
    }

    # --- Python environment --------------------------------------------------------------
    function Test-PythonEnv {
        if (-not (Test-Path -LiteralPath $Python)) { return $false }
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $Python -c "import fastapi, uvicorn" 2>$null
        $ok = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $previous
        return $ok
    }

    if (-not (Test-PythonEnv)) {
        Write-Step 'Setting up the Python environment (.venv)'
        if (Get-Command uv -ErrorAction SilentlyContinue) {
            Invoke-Native 'uv sync' { uv sync }
        } else {
            if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
                throw 'Python was not found. Install Python 3.14+ (or uv), or create the .venv from PyCharm, then run this again.'
            }
            if (-not (Test-Path -LiteralPath $Python)) {
                Invoke-Native 'python -m venv' { python -m venv .venv }
            }
            # Install the dependencies declared in pyproject.toml (no list to keep in sync here).
            $deps = & $Python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))"
            if ($LASTEXITCODE -ne 0) { throw 'Could not read dependencies from pyproject.toml.' }
            Invoke-Native 'pip install' { & $Python -m pip install --disable-pip-version-check @($deps) }
        }
    }

    # --- Front end -------------------------------------------------------------------------
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    $needsBuild = $Rebuild -or -not (Test-Path -LiteralPath $DistIndex)
    if (-not $needsBuild) {
        # Rebuild when any source file is newer than the last build.
        $builtAt = (Get-Item -LiteralPath $DistIndex).LastWriteTime
        $sources = @((Join-Path $Frontend 'src'), (Join-Path $Frontend 'index.html'), (Join-Path $Frontend 'package.json'), (Join-Path $Frontend 'vite.config.js'))
        $stale = Get-ChildItem -LiteralPath $sources -Recurse -File | Where-Object { $_.LastWriteTime -gt $builtAt } | Select-Object -First 1
        $needsBuild = [bool]$stale
    }

    if ($needsBuild -or $Dev) {
        if (-not $npm) {
            if ((Test-Path -LiteralPath $DistIndex) -and -not $Dev) {
                Write-Warning 'Node.js (npm) was not found, so the front end was not rebuilt. Serving the existing build.'
                $needsBuild = $false
            } else {
                throw 'Node.js (npm) is required to build the front end. Install it from https://nodejs.org and run this again.'
            }
        } elseif (-not (Test-Path -LiteralPath (Join-Path $Frontend 'node_modules'))) {
            Write-Step 'Installing front end packages (npm install)'
            Invoke-Native 'npm install' { npm.cmd --prefix $Frontend install }
        }
    }
    if ($needsBuild -and $npm) {
        Write-Step 'Building the front end'
        Invoke-Native 'npm run build' { npm.cmd --prefix $Frontend run build }
    }

    # --- Run -----------------------------------------------------------------------------------
    $vite = $null
    $server = $null
    try {
        Write-Step "Starting the server on $Url"
        $uvicornArgs = @('-m', 'uvicorn', 'main:app', '--port', "$Port")
        if ($Dev) { $uvicornArgs += '--reload' }
        $server = Start-Process -FilePath $Python -ArgumentList $uvicornArgs -WorkingDirectory $Root -NoNewWindow -PassThru
        $null = $server.Handle   # keeps the exit code readable after the process ends

        $openUrl = $Url
        if ($Dev) {
            Write-Step 'Starting the Vite dev server on http://localhost:5173'
            $env:API_TARGET = "http://127.0.0.1:$Port"
            $vite = Start-Process -FilePath 'npm.cmd' -ArgumentList @('--prefix', "`"$Frontend`"", 'run', 'dev', '--', '--strictPort') -WorkingDirectory $Root -NoNewWindow -PassThru
            $null = $vite.Handle
            $openUrl = 'http://localhost:5173'
        }

        $deadline = (Get-Date).AddSeconds(30)
        while (-not (Test-MarathonUp $Port)) {
            if ($server.HasExited) { throw "The server stopped unexpectedly (exit code $($server.ExitCode)). See the messages above." }
            if ((Get-Date) -gt $deadline) { throw "The server did not start answering on port $Port within 30 seconds." }
            Start-Sleep -Milliseconds 300
        }

        Write-Host ''
        Write-Host "Marathon is running at $openUrl" -ForegroundColor Green
        Write-Host 'Press Ctrl+C to stop.'
        Write-Host ''
        if (-not $NoBrowser) { Start-Process $openUrl }

        $server.WaitForExit()
    } finally {
        Stop-Tree $vite
        Stop-Tree $server
    }
} catch {
    Write-Host ''
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
