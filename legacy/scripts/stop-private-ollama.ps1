$ErrorActionPreference = "Stop"
$RuntimeRoot = Join-Path $env:LOCALAPPDATA "GP...T\ollama"
$PidPath = Join-Path $RuntimeRoot "server.json"
if (-not (Test-Path -LiteralPath $PidPath)) {
  Write-Output "The private GP...T Ollama service is not running."
  exit 0
}

$Marker = Get-Content -LiteralPath $PidPath -Raw | ConvertFrom-Json
if (
  $Marker.profile -cne "gp-dot-t-private-v1" -or
  $Marker.port -ne 11435 -or
  ($Marker.process_id -isnot [int] -and $Marker.process_id -isnot [long]) -or
  $Marker.process_id -lt 1 -or
  $Marker.process_id -gt [int]::MaxValue
) {
  throw "The private runtime marker is malformed; no process was stopped."
}
$ProcessId = [int]$Marker.process_id
$Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
if (-not $Process) {
  Remove-Item -LiteralPath $PidPath -Force
  Write-Output "The private Ollama process had already exited."
  exit 0
}

$ExpectedExecutable = [System.IO.Path]::GetFullPath([string]$Marker.executable)
$ActualExecutable = [System.IO.Path]::GetFullPath([string]$Process.ExecutablePath)
$ExpectedStartTicks = $null
if ($Marker.PSObject.Properties.Name -contains "process_start_utc_ticks") {
  if (
    ($Marker.process_start_utc_ticks -is [int] -or $Marker.process_start_utc_ticks -is [long]) -and
    $Marker.process_start_utc_ticks -gt [DateTime]::MinValue.Ticks -and
    $Marker.process_start_utc_ticks -le [DateTime]::MaxValue.Ticks
  ) {
    $ExpectedStartTicks = [long]$Marker.process_start_utc_ticks
  }
} elseif ($Marker.process_start_utc -is [DateTime]) {
  $ExpectedStartTicks = $Marker.process_start_utc.ToUniversalTime().Ticks
} elseif ($Marker.process_start_utc -is [string] -and $Marker.process_start_utc.Length -le 64) {
  try {
    $ExpectedStartTicks = [DateTimeOffset]::Parse([string]$Marker.process_start_utc).UtcDateTime.Ticks
  } catch {
    $ExpectedStartTicks = $null
  }
}
if ($null -eq $ExpectedStartTicks) {
  throw "The private runtime marker has no valid process creation time; no process was stopped."
}
$ActualStartTicks = $Process.CreationDate.ToUniversalTime().Ticks
$StartDifferenceMs = [Math]::Abs(($ExpectedStartTicks - $ActualStartTicks) / 10000.0)
$OwnsPrivatePort = Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $Process.ProcessId }
if ($ExpectedExecutable -cne $ActualExecutable -or $StartDifferenceMs -gt 20 -or $Process.CommandLine -notmatch "\bserve\b" -or -not $OwnsPrivatePort) {
  throw "The recorded process no longer matches the private service. No process was stopped."
}

Stop-Process -Id $Process.ProcessId
Wait-Process -Id $Process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PidPath -Force
Write-Output "Stopped the GP...T private Ollama service on port 11435. Model files and the digest pin were kept."
