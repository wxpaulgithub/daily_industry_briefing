param(
    [string]$ConfigPath = "",

    [string]$ServerHost = "",

    [string]$ServerUser = "",

    [string]$RemoteCookiePath = "",

    [int]$ServerPort = 0,
    [string]$SshKeyPath = "",

    [ValidateSet("zhihu", "bilibili", "both")]
    [string]$Site = "zhihu"
)

$ErrorActionPreference = "Stop"

function Get-CfgValue {
    param(
        [hashtable]$Cfg,
        [string]$Key,
        $Default = $null
    )
    if ($null -ne $Cfg -and $Cfg.ContainsKey($Key)) {
        return $Cfg[$Key]
    }
    return $Default
}

function Load-Config([string]$path) {
    if (-not $path) { return @{} }
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Config file not found: $path"
    }
    $cfg = & $path
    if ($null -eq $cfg) { return @{} }
    if ($cfg -isnot [hashtable]) {
        throw "Config file must return a hashtable."
    }
    return $cfg
}

function Write-Step([string]$msg) {
    Write-Host "[STEP] $msg" -ForegroundColor Cyan
}

function Require-Command([string]$name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Command not found: $name. Please install it and add to PATH."
    }
}

function Test-SiteCookie([string]$cookieFile, [string]$siteName) {
    if (-not (Test-Path -LiteralPath $cookieFile)) {
        return $false
    }
    $text = Get-Content -LiteralPath $cookieFile -Encoding UTF8 -Raw
    $escapedSite = [regex]::Escape($siteName)
    $pattern = '(?ms)^\[{0}\]\s*(.+?)(?=^\[|\z)' -f $escapedSite
    $m = [regex]::Match($text, $pattern)
    if (-not $m.Success) {
        return $false
    }
    $content = ($m.Groups[1].Value -replace '(?m)^\s*#.*$', '').Trim()
    return -not [string]::IsNullOrWhiteSpace($content)
}

try {
    Require-Command "scp"
    Require-Command "ssh"

    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = Split-Path -Parent $ScriptDir
    $DefaultConfigPath = Join-Path $ScriptDir "cookie_upload.config.ps1"
    if (-not $ConfigPath) {
        $ConfigPath = $DefaultConfigPath
    }
    $Cfg = @{}
    if (Test-Path -LiteralPath $ConfigPath) {
        $Cfg = Load-Config $ConfigPath
    }

    if (-not $ServerHost) {
        $ServerHost = [string](Get-CfgValue -Cfg $Cfg -Key "ServerHost" -Default "")
    }
    if (-not $ServerUser) {
        $ServerUser = [string](Get-CfgValue -Cfg $Cfg -Key "ServerUser" -Default "")
    }
    if (-not $RemoteCookiePath) {
        $RemoteCookiePath = [string](Get-CfgValue -Cfg $Cfg -Key "RemoteCookiePath" -Default "")
    }
    if ($ServerPort -le 0) {
        $ServerPort = [int](Get-CfgValue -Cfg $Cfg -Key "ServerPort" -Default 22)
    }
    if (-not $SshKeyPath) {
        $SshKeyPath = [string](Get-CfgValue -Cfg $Cfg -Key "SshKeyPath" -Default "")
    }

    if (-not $ServerHost -or -not $ServerUser -or -not $RemoteCookiePath) {
        throw "Missing required server settings. Please set ServerHost/ServerUser/RemoteCookiePath in config or params."
    }

    $VenvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
    $RefreshScript = Join-Path $ProjectRoot "scripts\update_cookie.py"
    $LocalCookieFile = Join-Path $ProjectRoot "runtime\cookie.txt"

    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "Python not found in venv: $VenvPython"
    }
    if (-not (Test-Path -LiteralPath $RefreshScript)) {
        throw "Script not found: $RefreshScript"
    }

    Write-Step "Refresh local cookie (site=$Site)"
    $args = @(
        $RefreshScript,
        "--site", $Site
    )

    & $VenvPython @args
    if ($LASTEXITCODE -ne 0) {
        throw "Cookie refresh script failed, exit code: $LASTEXITCODE"
    }

    Write-Step "Validate cookie file"
    switch ($Site) {
        "zhihu" {
            if (-not (Test-SiteCookie -cookieFile $LocalCookieFile -siteName "zhihu")) {
                throw "No valid [zhihu] cookie found, upload canceled."
            }
        }
        "bilibili" {
            if (-not (Test-SiteCookie -cookieFile $LocalCookieFile -siteName "bilibili")) {
                throw "No valid [bilibili] cookie found, upload canceled."
            }
        }
        "both" {
            if (-not (Test-SiteCookie -cookieFile $LocalCookieFile -siteName "zhihu")) {
                throw "No valid [zhihu] cookie found, upload canceled."
            }
            if (-not (Test-SiteCookie -cookieFile $LocalCookieFile -siteName "bilibili")) {
                throw "No valid [bilibili] cookie found, upload canceled."
            }
        }
    }

    Write-Step "Ensure remote directory exists"
    $remoteDir = Split-Path -Path $RemoteCookiePath -Parent
    $sshArgs = @("-p", "$ServerPort")
    if ($SshKeyPath) {
        $sshArgs += @("-i", $SshKeyPath)
    }
    $sshArgs += @(
        "$ServerUser@$ServerHost",
        "mkdir -p '$remoteDir'"
    )
    & ssh @sshArgs
    if ($LASTEXITCODE -ne 0) {
        throw "ssh remote prepare failed, exit code: $LASTEXITCODE"
    }

    Write-Step "Upload cookie.txt to server via scp"
    $scpArgs = @("-P", "$ServerPort")
    if ($SshKeyPath) {
        $scpArgs += @("-i", $SshKeyPath)
    }
    $scpArgs += @(
        $LocalCookieFile,
        "$ServerUser@$ServerHost`:$RemoteCookiePath"
    )
    & scp @scpArgs
    if ($LASTEXITCODE -ne 0) {
        throw "scp upload failed, exit code: $LASTEXITCODE"
    }

    $doneMsg = "Done: Cookie refreshed and uploaded to {0}@{1}:{2}" -f $ServerUser, $ServerHost, $RemoteCookiePath
    Write-Host $doneMsg -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "Failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
