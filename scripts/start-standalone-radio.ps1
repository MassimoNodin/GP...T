[CmdletBinding()]
param(
  [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.@:-]+$')] [string]$SshTarget = 'massimo-nodin@192.168.1.115',
  [string]$IdentityFile = "$env:USERPROFILE\.ssh\id_ed25519_ubuntu",
  [ValidatePattern('^/[A-Za-z0-9._/-]+$')] [string]$RemoteRepository = '/home/massimo-nodin/GP...T',
  [ValidateRange(1, 65535)] [int]$TunnelPort = 18765,
  [string]$Voice = 'en-AU-WilliamNeural',
  [ValidateSet('edge', 'windows')] [string]$TtsEngine = 'edge',
  [ValidateSet('off', 'rules', 'model')] [string]$ProactiveMode = 'rules',
  [ValidateRange(0, 1024)] [int]$Microphone,
  [ValidateRange(0, 1024)] [int]$CueOutput,
  [string]$LogDirectory,
  [switch]$CheckOnly,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
if ($DryRun -and $ProactiveMode -eq 'model') { throw 'Use rules mode for dry-run; model selection requires normal radio mode.' }
$repo = Split-Path -Parent $PSScriptRoot
$data = Join-Path $repo 'data'
$identity = (Resolve-Path -LiteralPath $IdentityFile).Path
$ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
$uv = (Get-Command uv.exe -ErrorAction Stop).Source
if (-not $LogDirectory) { $LogDirectory = Join-Path $data ('radio-tests\' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
$LogDirectory = [IO.Path]::GetFullPath($LogDirectory)
foreach ($directory in @($data, $LogDirectory)) {
  $parent = $directory
  while ($parent) {
    if ((Test-Path -LiteralPath $parent) -and ((Get-Item -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw 'Refusing redirected credential or log directory.'
    }
    $parent = Split-Path -Parent $parent
  }
  New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
$sshOptions = @('-i', $identity, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10')
$token = (@(& $ssh @sshOptions $SshTarget "cat -- '$RemoteRepository/data/.f1-engineer-control-token'") -join '').Trim()
if ($LASTEXITCODE -ne 0 -or $token -notmatch '^[A-Za-z0-9_-]{32,256}$') { throw 'Authenticated Ubuntu credentials are unavailable.' }
$tokenFile = Join-Path $data '.f1-engineer-ubuntu-control-token'
if ((Test-Path -LiteralPath $tokenFile) -and ((Get-Item -LiteralPath $tokenFile).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
  throw 'Refusing redirected credentials.'
}
if (-not (Test-Path -LiteralPath $tokenFile)) { [IO.File]::WriteAllText($tokenFile, '', [Text.Encoding]::ASCII) }
$permissions = [IO.File]::GetAccessControl($tokenFile, [Security.AccessControl.AccessControlSections]::Access)
$permissions.SetAccessRuleProtection($true, $false)
foreach ($rule in @($permissions.Access)) { $permissions.RemoveAccessRuleSpecific($rule) }
$permissions.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
  [Security.Principal.WindowsIdentity]::GetCurrent().User, 'FullControl', 'Allow'))
[IO.File]::SetAccessControl($tokenFile, $permissions)
[IO.File]::WriteAllText($tokenFile, $token, [Text.Encoding]::ASCII)
$tunnel = $null
$previousFaultHandler = $env:PYTHONFAULTHANDLER
$previousUnbuffered = $env:PYTHONUNBUFFERED
Start-Transcript -Path (Join-Path $LogDirectory 'launcher.log') -Append | Out-Null
Push-Location -LiteralPath $repo
try {
  $probe = [Net.Sockets.TcpClient]::new()
  $connected = $false
  try { $connected = $probe.ConnectAsync('127.0.0.1', $TunnelPort).Wait(1000) -and $probe.Connected }
  catch { $connected = $false } finally { $probe.Dispose() }
  if (-not $connected) {
    $forward = '127.0.0.1:{0}:127.0.0.1:8765' -f $TunnelPort
    $arguments = @('-i', ('"' + $identity + '"'), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
      '-o', 'ConnectTimeout=10', '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
      '-o', 'ServerAliveCountMax=3', '-N', '-T', '-L', $forward, $SshTarget)
    $tunnel = Start-Process -FilePath $ssh -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardError (Join-Path $LogDirectory 'ssh-stderr.log')
  }
  $api = "http://127.0.0.1:$TunnelPort"
  $deadline = (Get-Date).AddSeconds(30)
  while ($true) {
    if ($null -ne $tunnel) {
      $tunnel.Refresh()
      if ($tunnel.HasExited) { throw 'The SSH tunnel exited before readiness.' }
    }
    try {
      Invoke-RestMethod -Uri "$api/api/v2/engineer/live" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 3 | Out-Null
      break
    } catch {
      if ((Get-Date) -ge $deadline) { throw 'The updated authenticated engineer API did not become ready.' }
      Start-Sleep -Milliseconds 250
    }
  }
  $revision = (@(& git rev-parse HEAD) -join '').Trim()
  $manifest = @{ started_utc = (Get-Date).ToUniversalTime().ToString('o'); revision = $revision;
    api = $api; voice = $Voice; tts_engine = $TtsEngine; browser_required = $false; proactive_mode = $ProactiveMode; dry_run = [bool]$DryRun;
    microphone = $(if ($PSBoundParameters.ContainsKey('Microphone')) { $Microphone } else { 'system-default' });
    cue_output = $(if ($PSBoundParameters.ContainsKey('CueOutput')) { $CueOutput } else { 'system-default' }) }
  $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $LogDirectory 'manifest.json') -Encoding UTF8
  $env:PYTHONFAULTHANDLER = '1'
  $env:PYTHONUNBUFFERED = '1'
  Write-Output "Backend ready. No dashboard started. Logs: $LogDirectory"
  $radioArguments = @('run', '--extra', 'app', '--extra', 'radio', 'f1-engineer', 'radio',
    '--api-url', $api, '--token-file', $tokenFile, '--voice', $Voice, '--tts-engine', $TtsEngine,
    '--proactive-mode', $ProactiveMode, '--log-file', (Join-Path $LogDirectory 'radio.jsonl'))
  if ($DryRun) { $radioArguments += '--dry-run' }
  if ($PSBoundParameters.ContainsKey('Microphone')) { $radioArguments += @('--microphone', "$Microphone") }
  if ($PSBoundParameters.ContainsKey('CueOutput')) { $radioArguments += @('--cue-output', "$CueOutput") }
  if ($CheckOnly) { $radioArguments += '--once' }
  else { Write-Output 'Press the saved wheel button to listen. Console: listen, finish, status, tasks, quit.' }
  & $uv @radioArguments 2> (Join-Path $LogDirectory 'radio-stderr.log')
  if ($LASTEXITCODE -ne 0 -and -not ($CheckOnly -and $LASTEXITCODE -eq 2)) { throw "Radio exited with code $LASTEXITCODE; inspect the logs." }
} finally {
  if ($null -ne $tunnel) {
    $tunnel.Refresh()
    if (-not $tunnel.HasExited) { Stop-Process -Id $tunnel.Id }
    $tunnel.Dispose()
  }
  $env:PYTHONFAULTHANDLER = $previousFaultHandler
  $env:PYTHONUNBUFFERED = $previousUnbuffered
  Pop-Location
  Stop-Transcript | Out-Null
}
