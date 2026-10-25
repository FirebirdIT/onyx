#!/usr/bin/env bash
# Build the Deep X native engine bundle for this Mac.
#
#   desktop/native-engine/build-bundle.sh <version> [out-dir]
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

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  PLATFORM="darwin-aarch64"; NODE_ARCH="darwin-arm64"; PG_TARGET="aarch64-apple-darwin" ;;
  Darwin-x86_64) PLATFORM="darwin-x86_64";  NODE_ARCH="darwin-x64";   PG_TARGET="x86_64-apple-darwin" ;;
  *) echo "This script builds macOS bundles only." >&2; exit 1 ;;
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

copy_python() {
  local target="$1" src
  uv python install -q "$PYTHON_VERSION" --install-dir "$CACHE/python"
  src="$(find "$CACHE/python" -maxdepth 1 -type d -name "cpython-$PYTHON_VERSION-macos-*" | head -1)"
  [ -n "$src" ] || { echo "Python $PYTHON_VERSION was not installed" >&2; exit 1; }
  cp -R "$src" "$target"
  rm -f "$target/lib/python3.13/EXTERNALLY-MANAGED"
}

log "Python $PYTHON_VERSION and the backend packages"
copy_python "$BUNDLE/python"
# Export from uv.lock: it has the hashes of the wheels for every platform.
uv export --frozen --no-emit-project --no-default-groups --group backend -q \
  --project "$REPO" -o "$STAGE/backend-requirements.txt"
uv pip install -q --no-config --python "$BUNDLE/python/bin/python3" --no-deps --require-hashes \
  -r "$STAGE/backend-requirements.txt"

log "Backend source (no ee/)"
mkdir -p "$BUNDLE/backend"
for item in onyx shared_configs alembic alembic_tenants alembic.ini static keys assets; do
  cp -R "$REPO/backend/$item" "$BUNDLE/backend/"
done
find "$BUNDLE/backend" -name "__pycache__" -type d -prune -exec rm -rf {} +

log "Job Python and the chat code libraries"
copy_python "$BUNDLE/jobpython"
uv pip install -q --no-config --python "$BUNDLE/jobpython/bin/python3" -r "$HERE/job-requirements.txt"

log "Node.js $NODE_VERSION"
NODE_TGZ="node-$NODE_VERSION-$NODE_ARCH.tar.gz"
NODE_SHA="$(curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" | awk -v f="$NODE_TGZ" '$2==f {print $1}')"
[ -n "$NODE_SHA" ] || { echo "No checksum for $NODE_TGZ" >&2; exit 1; }
NODE_FILE="$(fetch "https://nodejs.org/dist/$NODE_VERSION/$NODE_TGZ" "$NODE_SHA")"
mkdir -p "$BUNDLE/node"
tar -xzf "$NODE_FILE" -C "$BUNDLE/node" --strip-components 1
rm -rf "$BUNDLE/node/include" "$BUNDLE/node/share"

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
  PATH="$BUNDLE/node/bin:$PATH" node tools/prepare-pdf-assets.cjs
  PATH="$BUNDLE/node/bin:$PATH" ./node_modules/.bin/next build
)
SERVER_JS="$(cd "$WEB_SRC/.next/standalone" && find . -name server.js -not -path "*/node_modules/*" | head -1)"
[ -n "$SERVER_JS" ] || { echo "next build made no standalone server.js" >&2; exit 1; }
WEB_ROOT="$(dirname "${SERVER_JS#./}")"
mkdir -p "$BUNDLE/web"
cp -R "$WEB_SRC/.next/standalone/." "$BUNDLE/web/"
mkdir -p "$BUNDLE/web/$WEB_ROOT/.next"
cp -R "$WEB_SRC/.next/static" "$BUNDLE/web/$WEB_ROOT/.next/static"
cp -R "$WEB_SRC/public" "$BUNDLE/web/$WEB_ROOT/public"

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
