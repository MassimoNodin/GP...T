[CmdletBinding()]
param(
  [ValidateRange(1, 65535)] [int]$Port = 8800,
  [switch]$NoBrowser
)

# Runs outside the project venv: updating from the panel replaces .venv, which a
# panel running inside it would hold open.
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$uv = (Get-Command uv.exe -ErrorAction Stop).Source
$arguments = @('run', '--no-project', 'python', '-m', 'scripts.control_panel', '--port', "$Port")
if ($NoBrowser) { $arguments += '--no-browser' }
Push-Location -LiteralPath $repo
try { & $uv @arguments } finally { Pop-Location }
