#!/usr/bin/env bash
# Build the Deep X native engine bundle for this Mac.
#
#   desktop/native-engine/build-bundle.sh <version> [out-dir]
#
# TARGET_ARCH=x86_64 builds the Intel bundle on an Apple silicon Mac. The
# Intel Python runs under Rosetta to pick its packages.
#
# The bundle holds everything the engine runs, with no Docker:
#   python/     CPython (python-build-standalone) with the backend packages
#   backend/    the backend source (Community Edition only)
#   jobpython/  a second CPython with the libraries that chat code imports
#   node/       Node.js, for the web server and sandbox-runtime
#   web/        the Next.js standalone server
#   postgres/   PostgreSQL (theseus-rs/postgresql-binaries)
#   srt/        Anthropic sandbox-runtime
# Output: deepx-engine-<version>-<platform>.tar.gz and its .sha256 file.
set -euo pipefail

VERSION="${1:?usage: build-bundle.sh <version> [out-dir]}"
OUT_DIR="${2:-$(pwd)/dist}"

PYTHON_VERSION="3.13.15"
NODE_VERSION="v24.22.1"
POSTGRES_VERSION="15.19.1"
SRT_VERSION="0.0.81"

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$REPO/desktop/native-engine"
CACHE="${DEEPX_BUNDLE_CACHE:-$HOME/.cache/deepx-engine-bundle}"

[ "$(uname -s)" = "Darwin" ] || { echo "This script builds macOS bundles only." >&2; exit 1; }
case "${TARGET_ARCH:-$(uname -m)}" in
  arm64|aarch64) PLATFORM="darwin-aarch64"; NODE_ARCH="darwin-arm64"; PG_TARGET="aarch64-apple-darwin"
                 PY_ARCH="aarch64"; SHARP_ARCH="arm64" ;;
  x86_64)        PLATFORM="darwin-x86_64";  NODE_ARCH="darwin-x64";   PG_TARGET="x86_64-apple-darwin"
                 PY_ARCH="x86_64"; SHARP_ARCH="x64" ;;
  *) echo "Unknown TARGET_ARCH ${TARGET_ARCH:-}" >&2; exit 1 ;;
esac

STAGE="$CACHE/stage-$PLATFORM"
BUNDLE="$STAGE/bundle"
mkdir -p "$CACHE/downloads" "$OUT_DIR"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE"

log() { printf '\n== %s\n' "$*"; }

# Download $1 to the cache once, check it against sha256 $2, and print the path.
fetch() {
  local url="$1" sha="$2" file
  file="$CACHE/downloads/$(basename "$1")"
  if [ ! -f "$file" ]; then
    curl -fsSL --retry 3 -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  echo "$sha  $file" | shasum -a 256 -c - >/dev/null || {
    echo "Checksum mismatch: $url" >&2; rm -f "$file"; exit 1; }
  echo "$file"
}

# Packages of uv.lock that have a CPU-specific wheel contain native code. uv must
# never compile one of them here: that build links against libraries of this
# Mac, which the bundle does not have. Pure Python source packages may build.
native_packages() {
  python3 - "$REPO/uv.lock" <<'PY'
import sys, tomllib
lock = tomllib.load(open(sys.argv[1], "rb"))
for package in lock.get("package", []):
    wheels = package.get("wheels", [])
    if any(not w["url"].endswith("-none-any.whl") for w in wheels):
        print(package["name"])
PY
}
# Native packages that may build here: checked to link only macOS system
# libraries (otool -L shows /usr/lib/libSystem.B.dylib only).
#   chonkie: Cython, publishes Intel macOS wheels only.
BUILD_ALLOWED=" chonkie "
NO_BUILD_ARGS=()
while read -r name; do
  case "$BUILD_ALLOWED" in *" $name "*) continue ;; esac
  NO_BUILD_ARGS+=(--only-binary "$name")
done < <(native_packages)

copy_python() {
  local target="$1" src
  uv python install -q "cpython-$PYTHON_VERSION-macos-$PY_ARCH-none" --install-dir "$CACHE/python"
  src="$(find "$CACHE/python" -maxdepth 1 -type d -name "cpython-$PYTHON_VERSION-macos-$PY_ARCH-*" | head -1)"
  [ -n "$src" ] || { echo "Python $PYTHON_VERSION was not installed" >&2; exit 1; }
  cp -R "$src" "$target"
  rm -f "$target/lib/python3.13/EXTERNALLY-MANAGED"
}

log "Python $PYTHON_VERSION and the backend packages"
copy_python "$BUNDLE/python"
# Export from uv.lock: it has the hashes of the wheels for every platform.
EXCLUDE=()
[ "$PLATFORM" = "darwin-x86_64" ] && EXCLUDE=(--no-emit-package cryptography --no-emit-package pyopenssl)
uv export --frozen --no-emit-project --no-default-groups --group backend -q ${EXCLUDE[@]+"${EXCLUDE[@]}"} \
  --project "$REPO" -o "$STAGE/backend-requirements.txt"
uv pip install -q --no-config ${NO_BUILD_ARGS[@]+"${NO_BUILD_ARGS[@]}"} --python "$BUNDLE/python/bin/python3" \
  --no-deps --require-hashes -r "$STAGE/backend-requirements.txt"
if [ "$PLATFORM" = "darwin-x86_64" ]; then
  uv pip install -q --no-config --no-build --python "$BUNDLE/python/bin/python3" \
    --no-deps --require-hashes -r "$HERE/intel-overrides.txt"
fi

log "Backend source (no ee/)"
mkdir -p "$BUNDLE/backend"
for item in onyx shared_configs alembic alembic_tenants alembic.ini static keys assets; do
  cp -R "$REPO/backend/$item" "$BUNDLE/backend/"
done
find "$BUNDLE/backend" -name "__pycache__" -type d -prune -exec rm -rf {} +

log "Job Python and the chat code libraries"
copy_python "$BUNDLE/jobpython"
uv pip install -q --no-config --no-build --python "$BUNDLE/jobpython/bin/python3" -r "$HERE/job-requirements.txt"

log "Node.js $NODE_VERSION"
NODE_TGZ="node-$NODE_VERSION-$NODE_ARCH.tar.gz"
NODE_SHA="$(curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" | awk -v f="$NODE_TGZ" '$2==f {print $1}')"
[ -n "$NODE_SHA" ] || { echo "No checksum for $NODE_TGZ" >&2; exit 1; }
NODE_FILE="$(fetch "https://nodejs.org/dist/$NODE_VERSION/$NODE_TGZ" "$NODE_SHA")"
mkdir -p "$BUNDLE/node"
tar -xzf "$NODE_FILE" -C "$BUNDLE/node" --strip-components 1
rm -rf "$BUNDLE/node/include" "$BUNDLE/node/share"

# The web build runs on a Node for this Mac's CPU, because its build tools are
# native modules. The output is JavaScript, the same for both CPUs.
BUILD_NODE_BIN="$BUNDLE/node/bin"
HOST_NODE_ARCH="darwin-$( [ "$(uname -m)" = "arm64" ] && echo arm64 || echo x64 )"
if [ "$HOST_NODE_ARCH" != "$NODE_ARCH" ]; then
  HOST_TGZ="node-$NODE_VERSION-$HOST_NODE_ARCH.tar.gz"
  HOST_SHA="$(curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" | awk -v f="$HOST_TGZ" '$2==f {print $1}')"
  HOST_FILE="$(fetch "https://nodejs.org/dist/$NODE_VERSION/$HOST_TGZ" "$HOST_SHA")"
  rm -rf "$STAGE/build-node" && mkdir -p "$STAGE/build-node"
  tar -xzf "$HOST_FILE" -C "$STAGE/build-node" --strip-components 1
  BUILD_NODE_BIN="$STAGE/build-node/bin"
fi

log "PostgreSQL $POSTGRES_VERSION"
PG_TGZ="postgresql-$POSTGRES_VERSION-$PG_TARGET.tar.gz"
PG_URL="https://github.com/theseus-rs/postgresql-binaries/releases/download/$POSTGRES_VERSION/$PG_TGZ"
PG_SHA="$(curl -fsSL "$PG_URL.sha256" | awk '{print $1}')"
PG_FILE="$(fetch "$PG_URL" "$PG_SHA")"
mkdir -p "$BUNDLE/postgres"
tar -xzf "$PG_FILE" -C "$BUNDLE/postgres" --strip-components 1

log "sandbox-runtime $SRT_VERSION"
mkdir -p "$BUNDLE/srt"
PATH="$BUNDLE/node/bin:$PATH" "$BUNDLE/node/bin/npm" install --silent --no-audit --no-fund \
  --omit=dev --prefix "$BUNDLE/srt" "@anthropic-ai/sandbox-runtime@$SRT_VERSION"

log "Web server (Next.js standalone)"
WEB_SRC="$STAGE/web-src"
mkdir -p "$WEB_SRC"
rsync -a --delete --exclude node_modules --exclude .next "$REPO/web/" "$WEB_SRC/"
(
  cd "$WEB_SRC"
  bun install --frozen-lockfile --silent
  export NEXT_PRIVATE_STANDALONE=true NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_BRAND=deepx
  export NODE_OPTIONS="--max-old-space-size=8192"
  PATH="$BUILD_NODE_BIN:$PATH" node tools/prepare-pdf-assets.cjs
  PATH="$BUILD_NODE_BIN:$PATH" ./node_modules/.bin/next build
)
SERVER_JS="$(cd "$WEB_SRC/.next/standalone" && find . -name server.js -not -path "*/node_modules/*" | head -1)"
[ -n "$SERVER_JS" ] || { echo "next build made no standalone server.js" >&2; exit 1; }
WEB_ROOT="$(dirname "${SERVER_JS#./}")"
mkdir -p "$BUNDLE/web"
cp -R "$WEB_SRC/.next/standalone/." "$BUNDLE/web/"
mkdir -p "$BUNDLE/web/$WEB_ROOT/.next"
cp -R "$WEB_SRC/.next/static" "$BUNDLE/web/$WEB_ROOT/.next/static"
cp -R "$WEB_SRC/public" "$BUNDLE/web/$WEB_ROOT/public"

# sharp (image optimization) is the only native module of the web server. The
# build installs it for this Mac, so put in the one for the target CPU.
SHARP_DIR="$(find "$BUNDLE/web/node_modules" -type d -path "*/@img/sharp-darwin-*" -prune | head -1)"
if [ -n "$SHARP_DIR" ] && [ "${SHARP_DIR##*-}" != "$SHARP_ARCH" ]; then
  SHARP_VERSION="$(jq -r .version "$SHARP_DIR/package.json")"
  VIPS_DIR="$(find "$BUNDLE/web/node_modules" -type d -path "*/@img/sharp-libvips-darwin-*" -prune | head -1)"
  VIPS_VERSION="$(jq -r .version "$VIPS_DIR/package.json")"
  SHARP_TMP="$STAGE/sharp-$SHARP_ARCH"
  rm -rf "$SHARP_TMP" && mkdir -p "$SHARP_TMP"
  PATH="$BUNDLE/node/bin:$PATH" "$BUNDLE/node/bin/npm" install --silent --no-audit --no-fund \
    --os=darwin --cpu="$SHARP_ARCH" --prefix "$SHARP_TMP" \
    "@img/sharp-darwin-$SHARP_ARCH@$SHARP_VERSION" "@img/sharp-libvips-darwin-$SHARP_ARCH@$VIPS_VERSION"
  find "$BUNDLE/web/node_modules" -depth -type d \( -name "*sharp-darwin-arm64*" -o -name "*sharp-darwin-x64*" \
    -o -name "*sharp-libvips-darwin-arm64*" -o -name "*sharp-libvips-darwin-x64*" \) -exec rm -rf {} +
  mkdir -p "$BUNDLE/web/node_modules/@img"
  cp -R "$SHARP_TMP/node_modules/@img/." "$BUNDLE/web/node_modules/@img/"
fi

log "Manifest"
cat > "$BUNDLE/manifest.json" <<EOF
{
  "version": "$VERSION",
  "platform": "$PLATFORM",
  "python": "$PYTHON_VERSION",
  "node": "$NODE_VERSION",
  "postgres": "$POSTGRES_VERSION",
  "sandboxRuntime": "$SRT_VERSION",
  "webServer": "$( [ "$WEB_ROOT" = "." ] && echo "web/server.js" || echo "web/$WEB_ROOT/server.js" )",
  "gitCommit": "$(git -C "$REPO" rev-parse HEAD)"
}
EOF

log "Archive"
ARCHIVE="$OUT_DIR/deepx-engine-$VERSION-$PLATFORM.tar.gz"
tar -czf "$ARCHIVE" -C "$BUNDLE" .
( cd "$OUT_DIR" && shasum -a 256 "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256" )
du -sh "$BUNDLE" "$ARCHIVE"
echo "Built $ARCHIVE"
