[CmdletBinding()]
param(
  [switch] $InstallModel,
  [switch] $AcceptModelUpdate,
  [string] $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
  [string] $DatabasePath = (Join-Path (Join-Path $PSScriptRoot "..") "data\f1-engineer.sqlite3")
)

$ErrorActionPreference = "Stop"
$ModelName = "qwen3:4b"
$ProfileName = "gp-dot-t-private-v1"
$Endpoint = "http://127.0.0.1:11435"
$Port = 11435
$RuntimeRoot = Join-Path $env:LOCALAPPDATA "GP...T\ollama"
$ModelsRoot = Join-Path $RuntimeRoot "models"
$LogsRoot = Join-Path $RuntimeRoot "logs"
$PidPath = Join-Path $RuntimeRoot "server.json"
$ResolvedDatabasePath = if ([System.IO.Path]::IsPathRooted($DatabasePath)) { $DatabasePath } else { Join-Path $RepositoryRoot $DatabasePath }
$DataRoot = Split-Path -Parent $ResolvedDatabasePath
$PinPath = Join-Path $DataRoot ".f1-engineer-ollama-model.json"

$OllamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $OllamaCommand -or -not $OllamaCommand.Source) {
  throw "Install Ollama before starting the private GP...T runtime."
}
$OllamaPath = $OllamaCommand.Source

$ExistingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($ExistingListener) {
  throw "Port 11435 is already in use. The script will not connect to or stop that service."
}

New-Item -ItemType Directory -Path $RuntimeRoot, $ModelsRoot, $LogsRoot, $DataRoot -Force | Out-Null
$LogSuffix = [guid]::NewGuid().ToString("N")
$StdoutPath = Join-Path $LogsRoot "ollama-$LogSuffix.stdout.log"
$StderrPath = Join-Path $LogsRoot "ollama-$LogSuffix.stderr.log"

$env:OLLAMA_HOST = "127.0.0.1:11435"
$env:OLLAMA_MODELS = $ModelsRoot
$env:OLLAMA_NO_CLOUD = "1"
$env:OLLAMA_NUM_PARALLEL = "1"
$env:OLLAMA_MAX_LOADED_MODELS = "1"
$env:OLLAMA_MAX_QUEUE = "1"
$env:OLLAMA_KEEP_ALIVE = "0"

$Server = $null
try {
  $Server = Start-Process -FilePath $OllamaPath -ArgumentList @("serve") -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutPath -RedirectStandardError $StderrPath

  $Ready = $false
  $Deadline = (Get-Date).AddSeconds(60)
  while ((Get-Date) -lt $Deadline) {
    if ($Server.HasExited) {
      throw "The private Ollama process exited during startup. Check its local log files."
    }
    try {
      $Version = Invoke-RestMethod -Uri "$Endpoint/api/version" -TimeoutSec 2
      if ($Version.version -is [string] -and $Version.version.Length -le 64) {
        $Ready = $true
        break
      }
    } catch {
      Start-Sleep -Milliseconds 400
    }
  }
  if (-not $Ready) {
    throw "The private Ollama service did not become ready within 60 seconds."
  }

  $VersionMatch = [regex]::Match([string]$Version.version, '^(\d+)\.(\d+)\.(\d+)$')
  if (-not $VersionMatch.Success -or [version]$Version.version -lt [version]"0.9.0") {
    throw "Ollama 0.9.0 or newer is required for Qwen3 routing. The private service will be stopped before model installation."
  }

  $PrivateListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.OwningProcess -eq $Server.Id -and $_.LocalAddress -eq "127.0.0.1" }
  if (-not $PrivateListener) {
    throw "The private Ollama process does not own the expected loopback listener."
  }

  if ($InstallModel) {
    & $OllamaPath pull $ModelName
    if ($LASTEXITCODE -ne 0) {
      throw "The local model download failed. No pin was written."
    }
    $Tags = Invoke-RestMethod -Uri "$Endpoint/api/tags" -TimeoutSec 5
    $Model = @($Tags.models | Where-Object { $_.name -ceq $ModelName } | Select-Object -First 1)
    if ($Model.Count -ne 1 -or $Model[0].digest -notmatch "^[0-9a-f]{64}$") {
      throw "Ollama did not return the exact model and full digest expected by GP...T."
    }
    if (($Model[0].remote_host -is [string] -and $Model[0].remote_host.Length -gt 0) -or ($Model[0].remote_model -is [string] -and $Model[0].remote_model.Length -gt 0)) {
      throw "A remote-backed model was returned. It will not be pinned."
    }
    $PinnedDigest = "sha256:$($Model[0].digest)"

    if (Test-Path -LiteralPath $PinPath) {
      $ExistingPin = Get-Content -LiteralPath $PinPath -Raw | ConvertFrom-Json
      if ($ExistingPin.digest -ne $PinnedDigest -and -not $AcceptModelUpdate) {
        throw "The model digest changed. The existing pin was preserved; pass -AcceptModelUpdate only after reviewing the change."
      }
    }
    $Pin = [ordered]@{
      schema_version = 1
      profile = $ProfileName
      endpoint = $Endpoint
      no_cloud_requested = $true
      model = $ModelName
      digest = $PinnedDigest
    }
    $PinTempPath = "$PinPath.$LogSuffix.tmp"
    $PinJson = $Pin | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($PinTempPath, $PinJson, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $PinTempPath -Destination $PinPath -Force
  }

  $ProcessMarker = [ordered]@{
    process_id = $Server.Id
    process_start_utc = (Get-Process -Id $Server.Id).StartTime.ToUniversalTime().ToString("o")
    executable = [System.IO.Path]::GetFullPath($OllamaPath)
    port = $Port
    profile = $ProfileName
  }
  [System.IO.File]::WriteAllText($PidPath, ($ProcessMarker | ConvertTo-Json -Compress), [System.Text.UTF8Encoding]::new($false))

  if ($InstallModel) {
    Write-Output "Private Ollama is ready at 127.0.0.1:11435 with the pinned Qwen3 4B model."
  } else {
    Write-Output "Private Ollama is ready at 127.0.0.1:11435."
    Write-Output "Install the model with -InstallModel when you want to enable Ask GP...T."
  }
  Write-Output "OLLAMA_NO_CLOUD=1 was requested; GP...T keeps cloud routing labeled unverified."
  Write-Output "Stop only this private service with .\scripts\stop-private-ollama.ps1."
} catch {
  if ($Server -and -not $Server.HasExited) {
    Stop-Process -Id $Server.Id -ErrorAction SilentlyContinue
  }
  Remove-Item -LiteralPath $PidPath -Force -ErrorAction SilentlyContinue
  throw
}
