# Build the Deep X native engine bundle for 64-bit Intel Windows.
#
#   powershell -ExecutionPolicy Bypass -File desktop\native-engine\build-bundle.ps1 <version> [out-dir]
#
# The Windows twin of build-bundle.sh. The bundle has the same layout:
#   python\     CPython (python-build-standalone) with the backend packages
#   backend\    the backend source (Community Edition only)
#   node\       Node.js, for the web server
#   web\        the Next.js standalone server
#   postgres\   PostgreSQL (theseus-rs/postgresql-binaries)
#   jobpython\  a second CPython with the libraries that chat code imports
# There is no srt\: sandbox-runtime supports only macOS and Linux. On Windows
# the code runner runs jobs in an AppContainer
# (backend/onyx/deepx/code_runner/windows_sandbox.py).
# Output: deepx-engine-<version>-windows-x86_64.tar.gz, its .sha256 file, and
# a native.json entry for the release.
#
# Needs: uv, bun 1.3.13 (older versions cannot read web/bun.lock), git, and Visual Studio Build Tools with "Desktop development
# with C++" (chonkie has no Windows wheel and is compiled here).
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Version,
    [Parameter(Position = 1)][string]$OutDir = (Join-Path (Get-Location) "dist")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$PythonVersion = "3.13.15"
$NodeVersion = "v24.22.1"
$PostgresVersion = "15.19.1"
$Platform = "windows-x86_64"

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Cache = if ($env:DEEPX_BUNDLE_CACHE) { $env:DEEPX_BUNDLE_CACHE } else { Join-Path $env:LOCALAPPDATA "deepx-engine-bundle" }
$Stage = Join-Path $Cache "stage-$Platform"
$Bundle = Join-Path $Stage "bundle"
$Downloads = Join-Path $Cache "downloads"

if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "This script builds the 64-bit Intel Windows bundle only."
}
$bunVersion = (& bun --version).Trim()
if ([version]$bunVersion -lt [version]"1.3.13") {
    throw "bun $bunVersion is too old to read web/bun.lock. Install bun 1.3.13 or later first on PATH."
}
# Remove-Item fails on paths over 260 characters, which node_modules has.
function Remove-Tree([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        & cmd.exe /c rd /s /q "\\?\$Path"
        if (Test-Path -LiteralPath $Path) { throw "Could not remove $Path" }
    }
}

New-Item -ItemType Directory -Force $Downloads, $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
Remove-Tree $Bundle
New-Item -ItemType Directory -Force $Bundle | Out-Null

function Log([string]$Text) { Write-Host "`n== $Text" }

# Run a program and stop when it fails. No param block, so that arguments
# such as -o reach the program instead of the PowerShell parameter binder.
function Run {
    $Program = $args[0]
    $Arguments = @($args | Select-Object -Skip 1)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

# Download $Url to the cache once, check it against SHA-256 $Sha, and return the path.
function Fetch([string]$Url, [string]$Sha) {
    $file = Join-Path $Downloads ([IO.Path]::GetFileName($Url))
    if (-not (Test-Path $file)) {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile "$file.part"
        Move-Item -Force "$file.part" $file
    }
    $actual = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
    if ($actual -ne $Sha.ToLowerInvariant()) {
        Remove-Item -Force $file
        throw "Checksum mismatch: $Url"
    }
    return $file
}

function Get-Text([string]$Url) {
    # Content is a string for text types and bytes for the others.
    $content = (Invoke-WebRequest -UseBasicParsing -Uri $Url).Content
    if ($content -is [byte[]]) { return [Text.Encoding]::UTF8.GetString($content) }
    return [string]$content
}

# Unpack the archive $File into $Target, without its top folder.
function Expand-Stripped([string]$File, [string]$Target) {
    $tmp = "$Target.unpack"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    New-Item -ItemType Directory -Force $tmp | Out-Null
    Run "$env:SystemRoot\System32\tar.exe" -xf $File -C $tmp
    $top = @(Get-ChildItem $tmp)
    if ($top.Count -ne 1 -or -not $top[0].PSIsContainer) { throw "$File does not have one top folder" }
    Move-Item $top[0].FullName $Target
    Remove-Item -Recurse -Force $tmp
}

function Copy-Python([string]$Target) {
    $pythonCache = Join-Path $Cache "python"
    Run uv python install -q "cpython-$PythonVersion-windows-x86_64-none" --install-dir $pythonCache
    $src = Get-ChildItem $pythonCache -Directory -Filter "cpython-$PythonVersion-windows-x86_64-*" | Select-Object -First 1
    if (-not $src) { throw "Python $PythonVersion was not installed" }
    Copy-Item -Recurse $src.FullName $Target
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Target "Lib\EXTERNALLY-MANAGED")
}

Log "Python $PythonVersion and the backend packages"
Copy-Python (Join-Path $Bundle "python")
$Python = Join-Path $Bundle "python\python.exe"

# Packages of uv.lock that have a CPU-specific wheel contain native code. uv
# must never compile one of them here, except the ones in $BuildAllowed:
#   chonkie: Cython, publishes no Windows wheel. It links only the Python and
#   Visual C++ runtime DLLs, which python-build-standalone ships.
$BuildAllowed = @("chonkie")
$nativeScript = @'
import sys, tomllib
lock = tomllib.load(open(sys.argv[1], "rb"))
for package in lock.get("package", []):
    wheels = package.get("wheels", [])
    if any(not w["url"].endswith("-none-any.whl") for w in wheels):
        print(package["name"])
'@
$nativePackages = @($nativeScript | & $Python - (Join-Path $Repo "uv.lock"))
if ($LASTEXITCODE -ne 0) { throw "Could not read uv.lock" }
$noBuildArgs = @()
foreach ($name in $nativePackages) {
    if ($BuildAllowed -notcontains $name.Trim()) { $noBuildArgs += @("--only-binary", $name.Trim()) }
}

# Export from uv.lock: it has the hashes of the wheels for every platform.
$requirements = Join-Path $Stage "backend-requirements.txt"
Run uv export --frozen --no-emit-project --no-default-groups --group backend -q --project $Repo -o $requirements
Run uv pip install -q --no-config @noBuildArgs --python $Python --no-deps --require-hashes -r $requirements

Log "Backend source (no ee\)"
$backend = Join-Path $Bundle "backend"
New-Item -ItemType Directory -Force $backend | Out-Null
foreach ($item in "onyx", "shared_configs", "alembic", "alembic_tenants", "alembic.ini", "static", "keys", "assets") {
    Copy-Item -Recurse (Join-Path $Repo "backend\$item") $backend
}
Get-ChildItem $backend -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Log "Job Python and the chat code libraries"
$jobPython = Join-Path $Bundle "jobpython"
Copy-Python $jobPython
Run uv pip install -q --no-config --no-build --python (Join-Path $jobPython "python.exe") -r (Join-Path $PSScriptRoot "job-requirements.txt")

Log "Node.js $NodeVersion"
$nodeZip = "node-$NodeVersion-win-x64.zip"
$nodeSha = (Get-Text "https://nodejs.org/dist/$NodeVersion/SHASUMS256.txt") -split "`n" |
    ForEach-Object { $f = $_.Trim() -split "\s+"; if ($f.Count -eq 2 -and $f[1] -eq $nodeZip) { $f[0] } } |
    Select-Object -First 1
if (-not $nodeSha) { throw "No checksum for $nodeZip" }
$nodeFile = Fetch "https://nodejs.org/dist/$NodeVersion/$nodeZip" $nodeSha
$nodeDir = Join-Path $Bundle "node"
Expand-Stripped $nodeFile $nodeDir
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $nodeDir "include"), (Join-Path $nodeDir "share")

Log "PostgreSQL $PostgresVersion"
$pgTgz = "postgresql-$PostgresVersion-x86_64-pc-windows-msvc.tar.gz"
$pgUrl = "https://github.com/theseus-rs/postgresql-binaries/releases/download/$PostgresVersion/$pgTgz"
$pgSha = ((Get-Text "$pgUrl.sha256").Trim() -split "\s+")[0]
$pgFile = Fetch $pgUrl $pgSha
Expand-Stripped $pgFile (Join-Path $Bundle "postgres")

Log "Web server (Next.js standalone)"
$webSrc = Join-Path $Stage "web-src"
# An isolated install (from an older build) also left node_modules in the
# workspace packages, which break a hoisted install. Start over then.
if (Test-Path (Join-Path $webSrc "node_modules\.bun")) { Remove-Tree $webSrc }
New-Item -ItemType Directory -Force $webSrc | Out-Null
# robocopy exit codes below 8 mean success.
& robocopy (Join-Path $Repo "web") $webSrc /MIR /XD node_modules .next /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
$savedPath = $env:Path
Push-Location $webSrc
try {
    # The bundled Node first: install scripts of packages need a recent Node.
    $env:Path = "$nodeDir;$savedPath"
    # Hoisted: a flat node_modules of real folders. Bun's default isolated
    # layout links packages with absolute Windows junctions into this build
    # folder, and the standalone copy then loses the dependencies of next. It
    # also keeps paths short (see windows_bundles_dir in engine/native/mod.rs).
    # A fresh .next: the Turbopack cache keeps failed lookups of an earlier
    # build, and an old standalone folder would be copied.
    Remove-Tree (Join-Path $webSrc ".next")
    Run bun install --frozen-lockfile --linker hoisted
    # Bun runs the prepare (build) scripts of the workspace packages only on a
    # fresh install, so build them here. opal uses the tokens of shared.
    foreach ($package in "lib\shared", "lib\opal") {
        Push-Location (Join-Path $webSrc $package)
        try { Run bun run build } finally { Pop-Location }
    }
    $env:NEXT_PRIVATE_STANDALONE = "true"
    $env:NEXT_TELEMETRY_DISABLED = "1"
    $env:NEXT_PUBLIC_BRAND = "deepx"
    $env:NODE_OPTIONS = "--max-old-space-size=8192"
    Run (Join-Path $nodeDir "node.exe") tools/prepare-pdf-assets.cjs
    Run (Join-Path $nodeDir "node.exe") node_modules/next/dist/bin/next build
} finally {
    $env:Path = $savedPath
    Pop-Location
}
$standalone = Join-Path $webSrc ".next\standalone"
$serverJs = Get-ChildItem $standalone -Recurse -Filter "server.js" -File |
    Where-Object { $_.FullName -notmatch "\\node_modules\\" } | Select-Object -First 1
if (-not $serverJs) { throw "next build made no standalone server.js" }
$webRoot = $serverJs.DirectoryName.Substring($standalone.Length).TrimStart("\")
$webDir = Join-Path $Bundle "web"
& robocopy $standalone $webDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed with exit code $LASTEXITCODE" }
$webApp = if ($webRoot) { Join-Path $webDir $webRoot } else { $webDir }
New-Item -ItemType Directory -Force (Join-Path $webApp ".next") | Out-Null
Copy-Item -Recurse (Join-Path $webSrc ".next\static") (Join-Path $webApp ".next\static")
Copy-Item -Recurse (Join-Path $webSrc "public") (Join-Path $webApp "public")

# sharp (image optimization) is the only native module of the web server. The
# build runs on this computer, so it already installed the win32-x64 build.
$sharp = Get-ChildItem (Join-Path $webDir "node_modules") -Recurse -Directory -Filter "sharp-win32-x64" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $sharp) { Write-Warning "The web server has no @img/sharp-win32-x64; image optimization falls back." }

Log "Manifest"
$webServer = if ($webRoot) { "web/$($webRoot.Replace('\', '/'))/server.js" } else { "web/server.js" }
$manifest = [ordered]@{
    version   = $Version
    platform  = $Platform
    python    = $PythonVersion
    node      = $NodeVersion
    postgres  = $PostgresVersion
    webServer = $webServer
    gitCommit = (& git -C $Repo rev-parse HEAD).Trim()
}
[IO.File]::WriteAllText((Join-Path $Bundle "manifest.json"), ($manifest | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))

Log "Archive"
$archive = Join-Path $OutDir "deepx-engine-$Version-$Platform.tar.gz"
# Python, with the \\?\ prefix: the bundle has paths over 260 characters, which
# tar.exe cannot read. Members are named "./<path>", as tar -C <dir> . makes
# them on macOS.
$archiveScript = @'
import os, sys, tarfile
root, out = "\\\\?\\" + os.path.abspath(sys.argv[1]), sys.argv[2]
with tarfile.open(out, "w:gz", compresslevel=6) as tar:
    tar.add(root, arcname=".", recursive=False)
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(dirs) + sorted(files):
            path = os.path.join(folder, name)
            tar.add(path, arcname="./" + os.path.relpath(path, root).replace(os.sep, "/"), recursive=False)
'@
$archiveScript | & $Python - $Bundle $archive
if ($LASTEXITCODE -ne 0) { throw "Could not write the archive" }
$sha = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLowerInvariant()
$size = (Get-Item $archive).Length
$name = [IO.Path]::GetFileName($archive)
[IO.File]::WriteAllText("$archive.sha256", "$sha  $name`n", (New-Object Text.UTF8Encoding $false))
$entry = [ordered]@{
    url    = "https://github.com/FirebirdIT/onyx/releases/download/native-v$Version/$name"
    sha256 = $sha
    size   = $size
}
$entryFile = Join-Path $OutDir "native-$Platform.json"
[IO.File]::WriteAllText($entryFile, (@{ $Platform = $entry } | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
Write-Host ("{0:N0} MB  {1}" -f ($size / 1MB), $archive)
Write-Host "native.json entry: $entryFile"
Write-Host "Built $archive"
