param(
  [string]$DatabasePath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'data\f1-engineer.sqlite3'),
  [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'GP...T\speech-runtime\v1.9.3')
)

$ErrorActionPreference = 'Stop'
$runtimeVersion = '1.9.3'
$sourceCommit = '371b5a7561823ab2bb32142d2751e35e7534727b'
$modelSha1 = 'c78c86eb1a8faa21b369bcd33207cc90d64ae9df'
$sourceRoot = Join-Path $InstallRoot "source-$($sourceCommit.Substring(0, 12))"
$buildRoot = Join-Path $InstallRoot "build-$($sourceCommit.Substring(0, 12))"
$modelRoot = Join-Path $InstallRoot 'models'
$modelPath = Join-Path $modelRoot 'ggml-tiny.en.bin'
$databaseDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($DatabasePath))
$pinPath = Join-Path $databaseDirectory '.f1-engineer-whisper-pin.json'

foreach ($commandName in @('git', 'cmake')) {
  if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
    throw "Install $commandName and Visual Studio C++ build tools, then rerun this script."
  }
}

New-Item -ItemType Directory -Force -Path $InstallRoot, $modelRoot, $databaseDirectory | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot '.git'))) {
  if (Test-Path -LiteralPath $sourceRoot) {
    throw "The source directory already exists but is not the expected Git checkout: $sourceRoot"
  }
  git init $sourceRoot | Out-Null
  git -C $sourceRoot remote add origin 'https://github.com/ggml-org/whisper.cpp.git'
  git -C $sourceRoot fetch --depth=1 origin $sourceCommit
  git -C $sourceRoot checkout --detach FETCH_HEAD
}
$checkedOutCommit = (git -C $sourceRoot rev-parse HEAD).Trim()
if ($checkedOutCommit -ne $sourceCommit) {
  throw "whisper.cpp source commit mismatch: expected $sourceCommit, found $checkedOutCommit"
}
$sourceChanges = @(git -C $sourceRoot status --porcelain=v1 --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the whisper.cpp source checkout state.' }
if ($sourceChanges.Count -gt 0) {
  throw 'The pinned whisper.cpp source checkout has local changes. Restore it to a clean checkout before building.'
}

cmake -S $sourceRoot -B $buildRoot `
  -DWHISPER_BUILD_TESTS=OFF `
  -DWHISPER_BUILD_EXAMPLES=ON `
  -DWHISPER_FFMPEG=OFF `
  -DGGML_NATIVE=OFF `
  -DGGML_CUDA=OFF `
  -DGGML_OPENCL=OFF `
  -DGGML_VULKAN=OFF `
  -DBUILD_SHARED_LIBS=OFF `
  -DCMAKE_BUILD_TYPE=Release
if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed.' }
cmake --build $buildRoot --config Release --target whisper-cli --parallel 4
if ($LASTEXITCODE -ne 0) { throw 'Building the pinned CPU whisper-cli failed.' }
$executables = @(Get-ChildItem -LiteralPath $buildRoot -Filter 'whisper-cli.exe' -File -Recurse)
if ($executables.Count -ne 1) { throw "Expected one built whisper-cli.exe; found $($executables.Count)." }
$executablePath = $executables[0].FullName

if (-not (Test-Path -LiteralPath $modelPath)) {
  $downloadPath = "$modelPath.download"
  if (Test-Path -LiteralPath $downloadPath) { Remove-Item -LiteralPath $downloadPath -Force }
  Invoke-WebRequest `
    -Uri 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin' `
    -OutFile $downloadPath
  $downloadSha1 = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA1).Hash.ToLowerInvariant()
  if ($downloadSha1 -ne $modelSha1) {
    Remove-Item -LiteralPath $downloadPath -Force
    throw "tiny.en model SHA-1 mismatch: expected $modelSha1, found $downloadSha1"
  }
  Move-Item -LiteralPath $downloadPath -Destination $modelPath
}
$actualModelSha1 = (Get-FileHash -LiteralPath $modelPath -Algorithm SHA1).Hash.ToLowerInvariant()
if ($actualModelSha1 -ne $modelSha1) {
  throw "tiny.en model SHA-1 mismatch: expected $modelSha1, found $actualModelSha1"
}

$executableSha256 = (Get-FileHash -LiteralPath $executablePath -Algorithm SHA256).Hash.ToLowerInvariant()
$modelSha256 = (Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash.ToLowerInvariant()
$dependencies = @(
  Get-ChildItem -LiteralPath (Split-Path -Parent $executablePath) -Filter '*.dll' -File |
    Sort-Object -Property Name |
    ForEach-Object {
      [ordered]@{
        name = $_.Name
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
      }
    }
)
if ($dependencies.Count -gt 32) { throw 'The runtime bundle has more than 32 DLL dependencies.' }
$dependencyIdentity = @(
  $dependencies | ForEach-Object { '{"name":"' + $_.name + '","sha256":"' + $_.sha256 + '"}' }
) -join ','
$identityJson = '{"dependencies":[' + $dependencyIdentity + '],"executable_sha256":"' + $executableSha256 + '","model_name":"tiny.en","model_sha256":"' + $modelSha256 + '","runtime":"whisper.cpp","runtime_version":"' + $runtimeVersion + '"}'
$sha256 = [System.Security.Cryptography.SHA256]::Create()
try {
  $identityDigest = [Convert]::ToHexString($sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($identityJson))).ToLowerInvariant()
} finally {
  $sha256.Dispose()
}
$manifest = [ordered]@{
  schema_version = 1
  runtime = 'whisper.cpp'
  runtime_version = $runtimeVersion
  executable_path = [System.IO.Path]::GetFullPath($executablePath)
  executable_sha256 = $executableSha256
  model_name = 'tiny.en'
  model_path = [System.IO.Path]::GetFullPath($modelPath)
  model_sha256 = $modelSha256
  dependencies = $dependencies
  runtime_id = $identityDigest
}
$manifestJson = ConvertTo-Json -InputObject $manifest -Depth 5
[System.IO.File]::WriteAllText(
  $pinPath,
  $manifestJson,
  [System.Text.UTF8Encoding]::new($false)
)

Write-Output "Pinned whisper.cpp $runtimeVersion CPU runtime and tiny.en model at $pinPath"
Write-Output "Runtime ID: $identityDigest"
