# Deep X on Windows without Docker

This guide is for the person or agent who continues this work on a Windows
computer. It tells you what to change, how to test it, and how to ship it.

## Current state

- macOS runs the engine without Docker (desktop 0.1.4 and later, native engine
  0.2.x). The engine is a bundle of Python, Node, Postgres and sandbox-runtime.
  The desktop app downloads it from the hub and starts each service as a
  process.
- Windows is on desktop **0.1.2**. That build is older than the Docker removal,
  so its setup still asks for Docker Desktop.
- The current desktop code compiles on Windows, but the engine does not start
  there. `is_supported()` in `desktop/src-tauri/src/engine/mod.rs` returns
  `true` only on macOS, and the `native` module compiles only on Unix.
- GitHub Actions is turned off on `FirebirdIT/onyx`. You build and publish
  releases on the Windows computer by hand.

## Goal

Ship a Windows desktop release that runs the engine with no Docker, like
macOS. Keep the macOS build working.

## Set up the Windows computer

1. Install Git, Rust (MSVC toolchain:
   `rustup default stable-x86_64-pc-windows-msvc`), Visual Studio Build Tools
   with "Desktop development with C++", Bun, `uv`, Node 22, and the GitHub CLI
   (then run `gh auth login`).
2. Install WebView2 if it is not there. Windows 11 has it.
3. Clone the repos:
   - `git clone https://github.com/FirebirdIT/onyx`
   - `git clone https://github.com/FirebirdIT/deepx-hub`
4. Copy the Tauri updater signing key and its password file from the Mac
   (`~/.tauri/deepx-updater.key` and the password file next to it). Use a USB
   drive or a password manager. **Never commit them, and never paste them in a
   chat.** Without this key, installed apps refuse the update.
5. Read `CLAUDE.md`, `web/AGENTS.md` and the desktop documents in
   `.agents/feature-map/` before you change code.

## What to change

### 1. Engine bundle for `windows-x86_64`

`desktop/native-engine/build-bundle.sh` builds macOS bundles only. Write a
Windows build (PowerShell, for example `build-bundle.ps1`) that makes the same
layout:

| Part | macOS source | Windows source |
| --- | --- | --- |
| Python + backend | python-build-standalone | python-build-standalone `x86_64-pc-windows-msvc` |
| Node + web server | nodejs.org `darwin-*` tarball | nodejs.org `win-x64` zip |
| Postgres 15 | theseus-rs/postgresql-binaries `*-apple-darwin` | same project, `x86_64-pc-windows-msvc` |
| sandbox-runtime | npm `@anthropic-ai/sandbox-runtime` | **not available on Windows** (see step 4) |
| Job Python | python-build-standalone | python-build-standalone Windows |

Keep these rules from the macOS script:

- Check the SHA-256 of every download.
- Install Python packages with `--require-hashes`, and `--only-binary` for
  native packages. Some packages may have no Windows wheel. Find them early.
- Swap `sharp` for the `win32-x64` build.
- Write `manifest.json` with `platform: "windows-x86_64"`.
- Make the archive and a `native.json` entry with `url`, `sha256` and `size`.

### 2. Desktop engine code (`desktop/src-tauri/src/engine/`)

These parts are Unix-only today. Make each one work on Windows with `#[cfg]`
branches or a small platform module:

- `mod.rs`: remove `#[cfg(unix)]` from `native`, and let `is_supported()`
  return `true` on Windows.
- `native/bundle.rs`:
  - `current_platform()` must return `"windows-x86_64"`.
  - Program paths differ: `python/python.exe`, `node/node.exe`,
    `postgres/bin/postgres.exe`, `jobpython/python.exe`.
  - Unpacking calls `/usr/bin/tar`. Windows has `tar.exe` in `System32`, or
    use the `flate2` and `tar` crates instead of a program.
- `native/process.rs`:
  - `command.process_group(0)` is Unix-only. On Windows, use the
    `CREATE_NEW_PROCESS_GROUP` creation flag and a Job Object, so that a stop
    also ends the child processes.
  - `signal_group()` calls `/bin/kill`. On Windows, close the Job Object or run
    `taskkill /T /F /PID <pid>`.
- `native/mod.rs`: the leftover cleanup uses `lsof` and `ps` to find processes
  by executable path. On Windows, use the `sysinfo` crate (it gives the
  executable path) or a Job Object. Kill only processes whose executable is in
  the bundles folder.
- `native/postgres.rs`: drop `unix_socket_directories` on Windows. Check that
  `initdb` and `pg_ctl` work in a folder path with spaces.
- `native/env.rs`: `PATH` uses `:` as a separator. Use
  `std::env::join_paths`, which uses `;` on Windows.

### 3. Hub

`deepx-hub/src/lib/native-engine.ts` accepts only `darwin-aarch64` and
`darwin-x86_64`. Add `windows-x86_64` to the enum, add a test, and deploy with
`vercel deploy --prod`.

### 4. Code runner (no sandbox-runtime on Windows)

The code runner (`backend/onyx/deepx/code_runner/`) runs user code inside
Anthropic sandbox-runtime (`srt`). `srt` supports only macOS and Linux.
`sandbox.py` also uses `os.killpg` and process groups.

Do **not** run model-written code without a sandbox. For the first Windows
release, turn the code runner off on Windows: do not start the `code_runner`
service, and do not set `CODE_INTERPRETER_BASE_URL`, so the tool does not
show. Add a Windows sandbox later as its own task (for example Windows
Sandbox, a restricted token with a Job Object, or WSL with bubblewrap).

### 5. Desktop app version

Raise the version in `desktop/src-tauri/Cargo.toml`,
`desktop/src-tauri/tauri.conf.json` and `desktop/package.json`. Use one
version for macOS and Windows.

## Test

1. Unit tests: `cd desktop/src-tauri` and run `cargo test`. Add Windows cases
   for the paths, the `PATH` separator and the platform name.
2. Build the bundle. Install it from the local archive
   (`install_local_archive` in `bundle.rs`), so you can test without the hub.
3. Run `bun run tauri dev` from `desktop/`. Check that:
   - Setup has 2 steps (Deep X Route, then start). There is no Docker step.
   - The engine starts, and `http://localhost:9999` opens the chat.
   - A chat with Deep X Route gets an answer.
   - After you quit the app, no `postgres.exe`, `python.exe` or `node.exe`
     from the bundle stays running (check Task Manager).
   - After you start again, the data is still there.
   - When you end one service in Task Manager, the supervisor restarts it.
4. Test on a clean Windows user account with no Docker installed.

## Release

The updater reads `latest.json` from the **newest** `desktop-v*` release. That
file must list every platform, or Macs stop getting updates. So one release
carries both platforms:

1. Build the Windows engine bundle and publish it in a new `native-v<ver>`
   release. Its `native.json` must keep the `darwin-aarch64` and
   `darwin-x86_64` entries and add `windows-x86_64`. Copy the macOS bundles
   from the last `native-v*` release, or rebuild them on the Mac.
2. Build the desktop app on Windows with the signing key:
   ```powershell
   $env:TAURI_SIGNING_PRIVATE_KEY = Get-Content -Raw <path-to-key>
   $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (Get-Content -Raw <path-to-password-file>).Trim()
   bun run tauri build --bundles nsis,msi
   ```
3. Build the macOS app of the same version on the Mac (universal build,
   `APPLE_SIGNING_IDENTITY="-"`, as for 0.1.6).
4. Create one `desktop-v<ver>` release with `gh release create`, with the
   full commit SHA as `--target`. Upload:
   - macOS: `.dmg`, `.app.tar.gz`, `.app.tar.gz.sig`
   - Windows: `-setup.exe`, `.msi`, and the updater file with its `.sig`
   - `latest.json` with `darwin-aarch64`, `darwin-x86_64` and
     `windows-x86_64` entries, each with `url` and `signature`
   - `downloads.json` with the new macOS and Windows links
5. Wait up to 5 minutes for the hub cache. Then check
   `https://deepx-hub.vercel.app/api/desktop/latest.json` and the download
   page.

If the Windows engine is not ready, you can still stop the Docker screen:
remove the Windows links from `downloads.json` in the next release, so that
nobody downloads 0.1.2.

## Rules

- Never print or commit secrets (signing key, API keys, `.env` files).
- Do not spend Deep X Route credits in tests. Tests make no paid API calls.
- Kill only processes that you started. Do not use a broad `taskkill /IM`.
- Do not read `backend/ee/`, `web/src/ee/` or `web/src/app/ee/`.
- When a part is done, commit, merge to `main` and push.
