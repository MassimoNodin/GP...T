[CmdletBinding()]
param(
  [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.@:-]+$')]
  [string]$SshTarget = 'massimo-nodin@192.168.1.115',
  [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519_ubuntu",
  [ValidatePattern('^/[A-Za-z0-9._/-]+$')]
  [string]$RemoteRepository = '/home/massimo-nodin/GP...T',
  [ValidateRange(1, 65535)] [int]$SshPort = 22,
  [ValidateRange(1, 65535)] [int]$ApiPort = 8765,
  [ValidateRange(1, 65535)] [int]$TunnelPort = 18765,
  [ValidateRange(1, 65535)] [int]$DashboardPort = 3000
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$web = Join-Path $repo 'web'
$data = Join-Path $repo 'data'
$identity = (Resolve-Path -LiteralPath $IdentityFile).Path
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath (Join-Path $web 'node_modules'))) {
  throw 'Install dashboard dependencies with: cd web; npm ci'
}
$sshOptions = @('-i', $identity, '-p', "$SshPort", '-o', 'IdentitiesOnly=yes',
  '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10')
$remoteTokenFile = "$RemoteRepository/data/.f1-engineer-control-token"
$token = (@(& $ssh @sshOptions $SshTarget "cat -- '$remoteTokenFile'") -join '').Trim()
if ($LASTEXITCODE -ne 0 -or $token -notmatch '^[A-Za-z0-9_-]{32,256}$') {
  throw 'Ubuntu credentials are unavailable. Start the authenticated service first.'
}
New-Item -ItemType Directory -Path $data -Force | Out-Null
$tokenFile = Join-Path $data '.f1-engineer-ubuntu-control-token'
if ((Test-Path -LiteralPath $tokenFile) -and
    ((Get-Item -LiteralPath $tokenFile).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
  throw 'Refusing to write credentials through a reparse point.'
}
[IO.File]::WriteAllText($tokenFile, $token, [Text.Encoding]::ASCII)
$permissions = [Security.AccessControl.FileSecurity]::new()
$permissions.SetAccessRuleProtection($true, $false)
$permissions.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
  [Security.Principal.WindowsIdentity]::GetCurrent().User, 'FullControl', 'Allow'))
Set-Acl -LiteralPath $tokenFile -AclObject $permissions
$previous = @{}
foreach ($name in @('F1_ENGINEER_API_URL', 'F1_ENGINEER_API_TOKEN_FILE', 'F1_ENGINEER_WEB_ORIGIN')) {
  $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$tunnel = $null
try {
  $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $TunnelPort)
  try { $probe.Start() } finally { $probe.Stop() }
  $forward = '127.0.0.1:{0}:127.0.0.1:{1}' -f $TunnelPort, $ApiPort
  $arguments = @('-i', ('"' + $identity + '"'), '-p', "$SshPort", '-o', 'IdentitiesOnly=yes',
    '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ExitOnForwardFailure=yes',
    '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', '-N', '-T',
    '-L', $forward, $SshTarget)
  $tunnel = Start-Process -FilePath $ssh -ArgumentList $arguments -PassThru -WindowStyle Hidden
  $env:F1_ENGINEER_API_URL = "http://127.0.0.1:$TunnelPort"
  $env:F1_ENGINEER_API_TOKEN_FILE = $tokenFile
  $env:F1_ENGINEER_WEB_ORIGIN = "http://127.0.0.1:$DashboardPort"
  $deadline = (Get-Date).AddSeconds(30)
  while ($true) {
    $tunnel.Refresh()
    if ($tunnel.HasExited) { throw 'The SSH tunnel exited before backend readiness.' }
    try {
      Invoke-RestMethod -Uri "$env:F1_ENGINEER_API_URL/api/v2/session-evidence/status" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 3 | Out-Null
      break
    } catch {
      if ((Get-Date) -ge $deadline) { throw 'Authenticated Ubuntu API did not become ready.' }
      Start-Sleep -Milliseconds 250
    }
  }
  Write-Output "Ubuntu connected. Windows dashboard: http://127.0.0.1:$DashboardPort"
  Push-Location -LiteralPath $web
  try {
    & $npm run dev -- --port $DashboardPort
    if ($LASTEXITCODE -ne 0) { throw "Dashboard exited with code $LASTEXITCODE" }
  } finally {
    Pop-Location
  }
} finally {
  if ($null -ne $tunnel) {
    $tunnel.Refresh()
    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id }
    $tunnel.Dispose()
  }
  foreach ($name in $previous.Keys) {
    [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
  }
}
