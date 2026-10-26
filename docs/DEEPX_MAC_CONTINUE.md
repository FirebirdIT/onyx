# Continue on the Mac: engine 0.2.5 and desktop 0.1.9

This note is for the Mac (you, or a Claude session on the Mac). The Windows
computer did its part of 2 releases. They bring the Python code tool to
Windows (an AppContainer sandbox, see step 4 of `docs/DEEPX_WINDOWS.md`). The
Mac finishes both, because the macOS bundles and app build only there and the
Tauri updater signing key stays on the Mac.

Both releases are built from commit
`db0744a447cf9e11e73972465434e1d85d91ad8c` (each draft's target). Read this
note on `main`; the target has an older copy of it.

## What the Windows computer did

- **Engine 0.2.5 (draft `native-v0.2.5`):** has
  `deepx-engine-0.2.5-windows-x86_64.tar.gz`, its `.sha256` file, and
  `native-windows.json` (the Windows entry). The Windows bundle now has
  `jobpython\` with the chat code libraries.
- **Desktop 0.1.9 (draft `desktop-v0.1.9`):** has
  `Deep.X_0.1.9_x64-setup.exe` and `Deep.X_0.1.9_x64_en-US.msi`. The
  `-setup.exe` is **not signed for the updater yet** (no `.sig` file).
- **Tested on Windows:** 21 code runner tests against the real AppContainer,
  and a job in the real engine (chart and Excel file out; the user's files,
  the internet and the engine's ports blocked).
- Drafts are hidden from the hub, so nothing is live yet.

## Rules

- Never print, paste or commit the signing key or its password.
- Do not spend Deep X Route credits in tests.
- `native.json` must list all 3 platforms, and `latest.json` of the newest
  `desktop-v*` release too, or some apps stop getting the engine or updates.
- Publish the engine first. Desktop 0.1.8 works with engine 0.2.5 (it does not
  start the runner on Windows), and desktop 0.1.9 works with engine 0.2.4 (no
  `jobpython\`, so no code tools).

## 1. Engine 0.2.5: build the macOS bundles

```bash
git fetch && git checkout db0744a447cf9e11e73972465434e1d85d91ad8c
mkdir -p ~/deepx-0.2.5
desktop/native-engine/build-bundle.sh 0.2.5 ~/deepx-0.2.5
TARGET_ARCH=x86_64 desktop/native-engine/build-bundle.sh 0.2.5 ~/deepx-0.2.5
```

## 2. Run the code runner tests on macOS

The Windows change also touched the macOS code path in
`backend/onyx/deepx/code_runner/` (`sandbox.py`, `workspace.py`,
`runner.py`). Run its tests against the new arm64 bundle before publishing:

```bash
T=$(mktemp -d) && tar -xzf ~/deepx-0.2.5/deepx-engine-0.2.5-darwin-aarch64.tar.gz -C $T
B=$(dirname "$(find $T -maxdepth 2 -name jobpython -type d | head -1)")
export DEEPX_TEST_JOB_PYTHON="$B/jobpython/bin/python3"
export DEEPX_TEST_SRT_COMMAND="'$B/node/bin/node' '$B/srt/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js'"
cd backend && pytest tests/external_dependency_unit/deepx/test_code_runner.py tests/unit/onyx/deepx/test_code_runner_workspace.py
cd .. && rm -rf $T
```

All tests must pass (2 Windows-only tests are skipped). If one fails, stop,
fix it on a branch, and the Windows bundle must be rebuilt from the new commit.

## 3. Engine 0.2.5: upload, write `native.json`, publish

```bash
cd ~/deepx-0.2.5
TAG=native-v0.2.5; V=0.2.5; BASE=https://github.com/FirebirdIT/onyx/releases/download/$TAG
gh release download $TAG -p native-windows.json
entry() { f=deepx-engine-$V-$1.tar.gz
  jq -n --arg u "$BASE/$f" --arg s "$(cut -d' ' -f1 $f.sha256)" --argjson n "$(stat -f%z $f)" \
    '{url:$u, sha256:$s, size:$n}'; }
jq -n --arg v "$V" --argjson a "$(entry darwin-aarch64)" --argjson x "$(entry darwin-x86_64)" \
  --slurpfile w native-windows.json \
  '{version:$v, platforms:({"darwin-aarch64":$a, "darwin-x86_64":$x} + $w[0].platforms)}' > native.json
jq . native.json
gh release upload $TAG deepx-engine-$V-darwin-*.tar.gz deepx-engine-$V-darwin-*.tar.gz.sha256 native.json
gh release delete-asset $TAG native-windows.json --yes
gh release edit $TAG --draft=false --latest
```

`native.json` must have version `0.2.5` and the 3 platforms. After up to 5
minutes, check that `https://deepx-hub.vercel.app/api/engine/native` shows
0.2.5 with `darwin-aarch64`, `darwin-x86_64` and `windows-x86_64`.

## 4. Desktop 0.1.9: sign the Windows installer

Keep the release files in `~/deepx-0.1.9`. Stay at the commit from step 1:

```bash
mkdir -p ~/deepx-0.1.9
gh release download desktop-v0.1.9 -p '*-setup.exe' -D ~/deepx-0.1.9
export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/deepx-updater.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat <password file>)"
cd desktop && bun install --frozen-lockfile
bun run tauri signer sign ~/deepx-0.1.9/Deep.X_0.1.9_x64-setup.exe   # writes the .sig next to it
gh release upload desktop-v0.1.9 ~/deepx-0.1.9/Deep.X_0.1.9_x64-setup.exe.sig
```

## 5. Desktop 0.1.9: build the macOS app

Stay in `desktop/` with the key variables set. Set the version only in the
working copy, and do not commit it:

```bash
sed -i '' 's/^version = .*/version = "0.1.9"/' src-tauri/Cargo.toml
jq '.version = "0.1.9" | .bundle.createUpdaterArtifacts = true' src-tauri/tauri.conf.json > t && mv t src-tauri/tauri.conf.json
jq '.version = "0.1.9"' package.json > t && mv t package.json
APPLE_SIGNING_IDENTITY="-" bun run tauri build --target universal-apple-darwin
B=src-tauri/target/universal-apple-darwin/release/bundle
cp $B/dmg/*.dmg $B/macos/*.app.tar.gz $B/macos/*.app.tar.gz.sig ~/deepx-0.1.9/
git checkout -- src-tauri/Cargo.toml src-tauri/tauri.conf.json package.json src-tauri/Cargo.lock; git status --short
```

The names must be `Deep.X_0.1.9_universal.dmg` and
`Deep.X_0.1.9_universal.app.tar.gz` (with `.sig`). Rename the `.app.tar.gz`
and its `.sig` if the build calls them `Deep X.app.tar.gz`.

## 6. Desktop 0.1.9: upload, `latest.json`, `downloads.json`, publish

```bash
cd ~/deepx-0.1.9
TAG=desktop-v0.1.9; V=0.1.9; BASE=https://github.com/FirebirdIT/onyx/releases/download/$TAG
MAC=Deep.X_${V}_universal.app.tar.gz; DMG=Deep.X_${V}_universal.dmg
WIN=Deep.X_${V}_x64-setup.exe; MSI=Deep.X_${V}_x64_en-US.msi
gh release upload $TAG $DMG $MAC $MAC.sig
jq -n --arg v "$V" --arg d "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg ms "$(cat $MAC.sig)" --arg mu "$BASE/$MAC" \
  --arg ws "$(cat $WIN.sig)" --arg wu "$BASE/$WIN" \
  '{version:$v, notes:"Deep X desktop \($v): the Python code tool works on Windows.", pub_date:$d, platforms:{
    "darwin-aarch64":{signature:$ms,url:$mu}, "darwin-x86_64":{signature:$ms,url:$mu},
    "windows-x86_64":{signature:$ws,url:$wu}}}' > latest.json
jq -n --arg v "$V" --arg b "$BASE" --arg dmg "$DMG" --arg win "$WIN" --arg msi "$MSI" \
  '{version:$v, assets:[
    {platform:"macos",kind:"dmg",name:$dmg,url:"\($b)/\($dmg)"},
    {platform:"windows",kind:"nsis",name:$win,url:"\($b)/\($win)"},
    {platform:"windows",kind:"msi",name:$msi,url:"\($b)/\($msi)"}]}' > downloads.json
gh release upload $TAG latest.json downloads.json
gh release view $TAG --json assets --jq '.assets[].name'
```

The release must have 8 files: `.dmg`, `.app.tar.gz` and its `.sig`,
`-setup.exe` and its `.sig`, `.msi`, `latest.json` and `downloads.json`. Then:

```bash
gh release edit desktop-v0.1.9 --draft=false --latest
```

After up to 5 minutes, check that
`https://deepx-hub.vercel.app/api/desktop/latest.json` shows 0.1.9 with 3
platforms, and that the download page links the 0.1.9 files.

## 7. Finish

Go back to `main`, mark this note done and update **Current state** in
`docs/DEEPX_WINDOWS.md` (engine 0.2.5 and desktop 0.1.9 live, code tools on
Windows released). Commit, merge to `main` and push. Delete `~/deepx-0.2.5`
and `~/deepx-0.1.9`.
