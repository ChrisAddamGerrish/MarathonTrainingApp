<#
.SYNOPSIS
  Starts the Marathon Training app and opens it in your browser.

.DESCRIPTION
  1. Makes sure the Python environment (.venv) exists and has the app's dependencies.
  2. Makes sure the React front end is built (installs npm packages / rebuilds when the
     source is newer than the last build).
  3. Makes sure the web app has a login (the first run asks for a username and password,
     saved to auth.env as a hash). The app shows its own sign-in page.
  4. Runs the FastAPI server, waits until it answers, then opens the browser.
  5. Runs the Caddy reverse proxy (see Caddyfile) so the app can be reached from other
     devices. Caddy is installed with winget if it is missing, and the first run asks where
     to listen (saved to caddy.env).
  Press Ctrl+C in this window to stop it.

  Written for Windows PowerShell 5.1 and PowerShell 7. Double-click start.bat if scripts
  are blocked by your execution policy.

.PARAMETER Port
  Port for the app. Default 8000.

.PARAMETER NoBrowser
  Don't open the browser.

.PARAMETER Dev
  Development mode: server auto-reloads on Python changes, and the Vite dev server runs
  (hot reload for the React code) at http://localhost:5173. The Caddy proxy is not started.

.PARAMETER Rebuild
  Force a fresh front end build.

.PARAMETER LocalOnly
  Don't start the Caddy proxy; the app is only reachable from this PC.

.PARAMETER ResetLogin
  Ask for a new username and password for the app's sign-in page. Works while the app is
  running, and signs out every device.

.EXAMPLE
  .\start.ps1
.EXAMPLE
  .\start.ps1 -Dev
.EXAMPLE
  .\start.ps1 -Port 8100 -NoBrowser
.EXAMPLE
  .\start.ps1 -LocalOnly
.EXAMPLE
  .\start.ps1 -ResetLogin
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$Dev,
    [switch]$Rebuild,
    [switch]$LocalOnly,
    [switch]$ResetLogin
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
    $CaddyFile = Join-Path $Root 'Caddyfile'
    $CaddyEnv = Join-Path $Root 'caddy.env'

    function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }

    # Run a native command, fail with a clear message if it exits non-zero.
    function Invoke-Native([string]$What, [scriptblock]$Command) {
        & $Command
        if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
    }

    # True when this app answers on the port (the health check needs no sign-in).
    function Test-MarathonUp([int]$P) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 -Uri "http://127.0.0.1:$P/api/health"
            return ($r.StatusCode -eq 200 -and $r.Content -like '*"app":"marathon"*')
        } catch {
            return $false
        }
    }

    # Run a native command that writes to stderr, returning its combined output. Windows
    # PowerShell 5.1 turns redirected stderr into terminating errors under 'Stop'.
    function Invoke-Captured([string]$What, [scriptblock]$Command) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { $output = & $Command 2>&1 | ForEach-Object { "$_" } } finally { $ErrorActionPreference = $previous }
        if ($LASTEXITCODE -ne 0) {
            $detail = $output -join [Environment]::NewLine
            throw ('{0} failed (exit code {1}):{2}{3}' -f $What, $LASTEXITCODE, [Environment]::NewLine, $detail)
        }
        return $output
    }

    # Kill a process and everything it started (npm.cmd -> node).
    function Stop-Tree($Proc) {
        if ($Proc -and -not $Proc.HasExited) {
            & taskkill.exe /PID $Proc.Id /T /F 2>&1 | Out-Null
        }
    }

    # --- Reverse proxy (Caddy) ------------------------------------------------------------
    # The proxy fronts the built app, so dev mode (Vite on :5173) runs without it.
    $UseProxy = -not ($LocalOnly -or $Dev)
    $caddyExe = $null
    if ($UseProxy) {
        # PATH first, then winget's folders: a winget install doesn't always add caddy to PATH.
        function Find-Caddy {
            $cmd = Get-Command caddy -ErrorAction SilentlyContinue
            if ($cmd) { return $cmd.Source }
            $roots = @("$env:LOCALAPPDATA\Microsoft\WinGet", "$env:ProgramFiles\WinGet") | ForEach-Object { "$_\Links", "$_\Packages" }
            return Get-ChildItem -LiteralPath $roots -Recurse -Filter caddy.exe -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty FullName
        }

        $caddyExe = Find-Caddy
        if (-not $caddyExe) {
            if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
                throw 'Caddy was not found. Install it from https://caddyserver.com/download (or run with -LocalOnly).'
            }
            Write-Step 'Installing Caddy (winget install CaddyServer.Caddy)'
            winget install --id CaddyServer.Caddy --exact --source winget --accept-package-agreements --accept-source-agreements
            # 0x8A15002B: already installed and up to date.
            if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { throw "winget install failed (exit code $LASTEXITCODE)." }
            # winget updates PATH for new terminals only, so reload it for this one.
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
            $caddyExe = Find-Caddy
            if (-not $caddyExe) { throw 'Caddy was installed but could not be found. Open a new terminal and run this again.' }
        }

        if (-not (Test-Path -LiteralPath $CaddyEnv)) {
            Write-Step 'Setting up the Caddy proxy (saved to caddy.env)'
            $site = Read-Host 'Domain name for HTTPS (leave blank to serve plain HTTP on port 8080)'
            if (-not $site) { $site = ':8080' }
            # Let's Encrypt only issues for real public domains; home-network names need Caddy's own CA.
            $tls = 'public'
            if ($site -notlike ':*') {
                $answer = Read-Host "Is $site a public domain whose DNS points at your internet IP? (y/N)"
                if ($answer -notmatch '^(y|yes)$') { $tls = 'internal' }
            }
            Set-Content -LiteralPath $CaddyEnv -Encoding ascii -Value @(
                '# Created by start.ps1. Delete this file to choose again.'
                "MARATHON_SITE=$site"
                "MARATHON_TLS=$tls"
            )
        }

        $env:MARATHON_APP_PORT = "$Port"
        $null = Invoke-Captured 'Checking the Caddyfile' { & $caddyExe validate --config $CaddyFile --adapter caddyfile --envfile $CaddyEnv }
        $publicSite = ((Get-Content -LiteralPath $CaddyEnv) -match '^MARATHON_SITE=' | Select-Object -Last 1) -replace '^MARATHON_SITE=', ''
    }

    # --- Python environment --------------------------------------------------------------
    function Test-PythonEnv {
        if (-not (Test-Path -LiteralPath $Python)) { return $false }
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        & $Python -c "import fastapi, sqlalchemy, uvicorn" 2>$null
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

    # --- Web app login -----------------------------------------------------------------------
    # auth.env holds the sign-in page's login (see backend/app/core/auth.py). The app re-reads it
    # when it changes, so a reset applies straight away, even while the app is running.
    $AuthFile = Join-Path $Root 'auth.env'
    $hasLogin = (Test-Path -LiteralPath $AuthFile) -and [bool]((Get-Content -LiteralPath $AuthFile) -match '^MARATHON_PASSWORD_HASH=.')
    if ($ResetLogin -or -not $hasLogin) {
        if ($hasLogin) {
            Write-Step 'Changing the sign-in login (auth.env)'
        } else {
            Write-Step 'Setting up the sign-in login (saved to auth.env)'
        }
        Invoke-Native 'Saving the login' { & $Python -m backend.app.core.auth }
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
    $proxy = $null
    try {
        Write-Step "Starting the server on $Url"
        $uvicornArgs = @('-m', 'uvicorn', 'backend.app.main:app', '--port', "$Port")
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

        if ($UseProxy) {
            Write-Step "Starting the Caddy reverse proxy on $publicSite"
            $proxy = Start-Process -FilePath $caddyExe -ArgumentList @('run', '--config', "`"$CaddyFile`"", '--adapter', 'caddyfile', '--envfile', "`"$CaddyEnv`"") -WorkingDirectory $Root -NoNewWindow -PassThru
            $null = $proxy.Handle
            Start-Sleep -Seconds 2
            if ($proxy.HasExited) { throw "Caddy stopped unexpectedly (exit code $($proxy.ExitCode)). See the messages above." }
        }

        Write-Host ''
        Write-Host "Marathon is running at $openUrl" -ForegroundColor Green
        if ($UseProxy) {
            $shown = if ($publicSite -like ':*') { "http://<this PC's IP>$publicSite" } else { "https://$publicSite" }
            Write-Host "Reachable from other devices at $shown (login required)" -ForegroundColor Green
        }
        Write-Host 'Press Ctrl+C to stop.'
        Write-Host ''
        if (-not $NoBrowser) { Start-Process $openUrl }

        $server.WaitForExit()
    } finally {
        Stop-Tree $proxy
        Stop-Tree $vite
        Stop-Tree $server
    }
} catch {
    Write-Host ''
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
