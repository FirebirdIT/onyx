# Continue on the Mac: Windows release of Deep X

This note is for the Mac (you, or a Claude session on the Mac). The Windows
computer did its part. The Mac finishes the release, because the Tauri
updater signing key stays on the Mac and the macOS app builds only there.
Background: `docs/DEEPX_WINDOWS.md`.

## What the Windows computer did

- The engine code runs on Windows without Docker. It was tested in the app.
- The hub accepts `windows-x86_64` in production (deployed 2026-10-26).
- **Engine 0.2.3 (live):** the Mac published `native-v0.2.3`. The Windows
  computer then added `deepx-engine-0.2.3-windows-x86_64.tar.gz` (built from
  the same commit, `4e9c7d210`) with its `.sha256` file, and a `native.json`
  with all 3 platforms. The hub serves the 3 platforms.
- **Desktop 0.1.8 (draft):** the draft release `desktop-v0.1.8` has the
  Windows installers `Deep.X_0.1.8_x64-setup.exe` and
  `Deep.X_0.1.8_x64_en-US.msi`, built from `c71c21181` (the draft's target).
  The `-setup.exe` is **not signed for the updater yet** (no `.sig` file),
  because the key is on the Mac. Drafts are hidden from the hub.
- **Tested:** the `-setup.exe` installed on Windows; the app downloaded engine
  0.2.3 from the live hub, started it and opened the sign-up page; quitting
  stopped every engine process. The app was then uninstalled.

Read this note on `main`. Step 2 checks out the draft's target, which has an
older copy of it.

## Rules

- Never print, paste or commit the signing key or its password.
- Do not spend Deep X Route credits in tests.
- `latest.json` of the newest `desktop-v*` release must list every platform,
  or some installed apps stop getting updates.

## 1. Engine 0.2.3 (done, keep Windows in it)

Nothing to do, but check that
`https://deepx-hub.vercel.app/api/engine/native` shows version 0.2.3 with
`darwin-aarch64`, `darwin-x86_64` and `windows-x86_64`.

From now on, every `native-v*` release needs a Windows bundle too, or Windows
apps cannot get the engine of that version. Build it on the Windows computer
(`desktop/native-engine/build-bundle.ps1 <version>`) from the same commit. If
you upload a new `native.json`, keep the `windows-x86_64` entry. Each bundle
has its version inside, and the app refuses a bundle whose version is not the
one in `native.json`, so never point a newer `native.json` at an older bundle.

## 2. Sign the Windows installer

Work in the repo, at the commit the Windows installers were built from, and
keep all release files in one folder, `~/deepx-0.1.8`:

```bash
git fetch && git checkout "$(gh release view desktop-v0.1.8 --json targetCommitish --jq .targetCommitish)"
mkdir -p ~/deepx-0.1.8
gh release download desktop-v0.1.8 -p '*-setup.exe' -D ~/deepx-0.1.8
export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/deepx-updater.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="$(cat <password file>)"
cd desktop && bun install --frozen-lockfile
bun run tauri signer sign ~/deepx-0.1.8/Deep.X_0.1.8_x64-setup.exe   # writes the .sig next to it
gh release upload desktop-v0.1.8 ~/deepx-0.1.8/Deep.X_0.1.8_x64-setup.exe.sig
```

The updater on Windows uses only the `-setup.exe`, so the `.msi` needs no
signature.

## 3. Build the macOS app 0.1.8

Stay in `desktop/` at the same commit, with the key variables from step 2
still set. Set the version only in your working copy, as the release workflow
does (`.github/workflows/deepx-desktop-release.yml`), and do not commit it:

```bash
sed -i '' 's/^version = .*/version = "0.1.8"/' src-tauri/Cargo.toml
jq '.version = "0.1.8" | .bundle.createUpdaterArtifacts = true' src-tauri/tauri.conf.json > t && mv t src-tauri/tauri.conf.json
jq '.version = "0.1.8"' package.json > t && mv t package.json
APPLE_SIGNING_IDENTITY="-" bun run tauri build --target universal-apple-darwin
B=src-tauri/target/universal-apple-darwin/release/bundle
cp $B/dmg/*.dmg $B/macos/*.app.tar.gz $B/macos/*.app.tar.gz.sig ~/deepx-0.1.8/
git checkout -- src-tauri/Cargo.toml src-tauri/tauri.conf.json package.json
```

Check that the names are `Deep.X_0.1.8_universal.dmg` and
`Deep.X_0.1.8_universal.app.tar.gz` (with `.sig`), as in 0.1.7. Rename the
`.app.tar.gz` and its `.sig` if the build calls them `Deep X.app.tar.gz`.

## 4. Upload, and write `latest.json` and `downloads.json`

```bash
cd ~/deepx-0.1.8
TAG=desktop-v0.1.8; V=0.1.8; BASE=https://github.com/FirebirdIT/onyx/releases/download/$TAG
MAC=Deep.X_${V}_universal.app.tar.gz; DMG=Deep.X_${V}_universal.dmg
WIN=Deep.X_${V}_x64-setup.exe; MSI=Deep.X_${V}_x64_en-US.msi
gh release upload $TAG $DMG $MAC $MAC.sig
jq -n --arg v "$V" --arg d "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg ms "$(cat $MAC.sig)" --arg mu "$BASE/$MAC" \
  --arg ws "$(cat $WIN.sig)" --arg wu "$BASE/$WIN" \
  '{version:$v, notes:"Deep X desktop \($v): Windows runs the engine without Docker.", pub_date:$d, platforms:{
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

The release must now have 8 files: `.dmg`, `.app.tar.gz` and its `.sig`,
`-setup.exe` and its `.sig`, `.msi`, `latest.json` and `downloads.json`.

## 5. Publish and check

1. Publish:
   ```bash
   gh release edit desktop-v0.1.8 --draft=false --latest
   ```
2. After up to 5 minutes, check:
   - `https://deepx-hub.vercel.app/api/desktop/latest.json` shows 0.1.8 with 3
     platforms.
   - The download page links the 0.1.8 `.dmg`, `-setup.exe` and `.msi`.
   - A Mac on 0.1.7 offers the update to 0.1.8.
3. Windows 0.1.2 has no working update path (it still asks for Docker), so its
   users install 0.1.8 from the download page.
4. Update `docs/DEEPX_WINDOWS.md` (Current state: released), commit, merge to
   `main` and push. Delete `~/deepx-0.1.8`.
