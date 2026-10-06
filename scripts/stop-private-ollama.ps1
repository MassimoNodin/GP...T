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
if ($Marker.process_start_utc -isnot [string] -or $Marker.process_start_utc.Length -gt 64) {
  throw "The private runtime marker has no valid process creation time; no process was stopped."
}
$Process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
if (-not $Process) {
  Remove-Item -LiteralPath $PidPath -Force
  Write-Output "The private Ollama process had already exited."
  exit 0
}

$ExpectedExecutable = [System.IO.Path]::GetFullPath([string]$Marker.executable)
$ActualExecutable = [System.IO.Path]::GetFullPath([string]$Process.ExecutablePath)
$ExpectedStart = [DateTimeOffset]::Parse([string]$Marker.process_start_utc).UtcDateTime
$ActualStart = $Process.CreationDate.ToUniversalTime()
$StartDifferenceMs = [Math]::Abs(($ExpectedStart - $ActualStart).TotalMilliseconds)
$OwnsPrivatePort = Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -eq $Process.ProcessId }
if ($ExpectedExecutable -cne $ActualExecutable -or $StartDifferenceMs -gt 20 -or $Process.CommandLine -notmatch "\bserve\b" -or -not $OwnsPrivatePort) {
  throw "The recorded process no longer matches the private service. No process was stopped."
}

Stop-Process -Id $Process.ProcessId
Wait-Process -Id $Process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $PidPath -Force
Write-Output "Stopped the GP...T private Ollama service on port 11435. Model files and the digest pin were kept."
