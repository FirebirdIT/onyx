# Deep X on Windows without Docker

This guide is for the person or agent who continues this work on a Windows
computer. It tells you what to change, how to test it, and how to ship it.

## Current state

- macOS runs the engine without Docker (desktop 0.1.4 and later, native engine
  0.2.x). The engine is a bundle of Python, Node, Postgres and sandbox-runtime.
  The desktop app downloads it from the hub and starts each service as a
  process.
- Desktop **0.1.8** is released for macOS and Windows (2026-10-26). Windows
  runs the engine without Docker. Windows 0.1.2 still asks for Docker and has
  no update path, so its users install 0.1.8 from the download page.
- The desktop engine code now supports Windows (steps 2 and 4 below are done),
  and `desktop/native-engine/build-bundle.ps1` builds the Windows bundle
  (step 1). The hub accepts `windows-x86_64` (step 3, deployed on
  2026-10-26).
- Tested in the app (`bun run tauri dev` with a local 0.2.2 bundle):
  setup shows 2 steps and no Docker step; the engine starts in about 2.5
  minutes on the first run (77 s of that is unpacking) and 40 s after that;
  `http://localhost:9999` opens Deep X and sign-up and log-in work; an ended
  web server comes back; quitting stops every engine process and Postgres
  shuts down cleanly; the data is still there after a restart; and when the
  app is force-ended, all engine processes end with it. Not tested yet: a
  chat with Deep X Route (it needs a key and spends credits) and a clean
  Windows user account.
- Engine 0.2.3 is live for macOS and Windows (`native-v0.2.3`).
- **Next, on the Windows computer: engine 0.2.4.** The draft `native-v0.2.4`
  has the macOS bundles and `native-darwin.json`, built from commit
  `3af008cfb` (the draft's target). Drafts are hidden from the hub. To
  finish it:
  1. Check out the draft's target commit and build the Windows bundle:
     `build-bundle.ps1 0.2.4`.
  2. Upload the `.tar.gz` and its `.sha256` file to the draft.
  3. Write `native.json` with version `0.2.4` and all 3 platforms: copy the
     2 `darwin-*` entries from `native-darwin.json`, and add
     `windows-x86_64`. Upload it, and delete `native-darwin.json` from the
     release.
  4. Publish: `gh release edit native-v0.2.4 --draft=false --latest`.
  5. After up to 5 minutes, check that
     `https://deepx-hub.vercel.app/api/engine/native` shows 0.2.4 with the
     3 platforms.
- GitHub Actions is turned off on `FirebirdIT/onyx`. You build and publish
  releases on the Windows computer by hand.

## Goal

Ship a Windows desktop release that runs the engine with no Docker, like
macOS. Keep the macOS build working.

## Set up the Windows computer

1. Install Git, Rust (MSVC toolchain:
   `rustup default stable-x86_64-pc-windows-msvc`), Visual Studio Build Tools
   with "Desktop development with C++", Bun **1.3.13** (older versions cannot
   read `web/bun.lock`; check that `bun --version` finds the new one first on
   `PATH`), `uv`, Node 22, and the GitHub CLI (then run `gh auth login`).
   `winget install Rustlang.Rustup astral-sh.uv` installs Rust and `uv`.
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

### 1. Engine bundle for `windows-x86_64` (done)

Build it with:

```powershell
powershell -ExecutionPolicy Bypass -File desktop\native-engine\build-bundle.ps1 <version> [out-dir]
```

It makes `deepx-engine-<version>-windows-x86_64.tar.gz`, its `.sha256` file
and `native-windows-x86_64.json` (the `native.json` entry with `url`, `sha256`
and `size`). The staging folder is `%LOCALAPPDATA%\deepx-engine-bundle`. The
Next.js build needs about 8 GB of free memory.

| Part | macOS source | Windows source |
| --- | --- | --- |
| Python + backend | python-build-standalone | python-build-standalone `windows-x86_64` |
| Node + web server | nodejs.org `darwin-*` tarball | nodejs.org `win-x64` zip |
| Postgres 15 | theseus-rs/postgresql-binaries `*-apple-darwin` | same project, `x86_64-pc-windows-msvc` |
| sandbox-runtime | npm `@anthropic-ai/sandbox-runtime` | **not in the bundle** (see step 4) |
| Job Python | python-build-standalone | **not in the bundle** (only the code runner uses it) |

Things that differ from the macOS script, and why:

- Every Python package has a Windows wheel except `chonkie`, which is compiled
  with Visual C++ during the build. The CUDA, `triton`, `caio` and
  `mitmproxy-linux` packages are Linux-only and are skipped.
- The web build uses `bun install --linker hoisted`. Bun's default isolated
  layout links packages with absolute Windows junctions into the build folder,
  and the standalone copy then loses the dependencies of `next` (the server
  fails with `Cannot find module '@swc/helpers/...'`). Hoisted also makes the
  paths shorter.
- Bun runs the `prepare` builds of the workspace packages (`lib/shared`,
  `lib/opal`) only on a fresh install, so the script builds them itself. It
  also deletes `.next` first: the Turbopack cache keeps failed lookups of an
  earlier build (`Package path ./tokens.css ... no valid target file`).
- The web server builds on Windows x64, so `sharp` is already the `win32-x64`
  build. There is nothing to swap.
- The archive is written by Python with `\\?\` paths, because `tar.exe` and
  PowerShell's `Remove-Item` fail on paths over 260 characters.

### 2. Desktop engine code (`desktop/src-tauri/src/engine/`) (done)

- `mod.rs`: `native` compiles on every platform. `is_supported()` is true
  where `current_platform()` has a name: macOS and Windows x86_64.
- `native/bundle.rs`: `current_platform()` returns `"windows-x86_64"`. The
  program paths are `python\python.exe`, `node\node.exe` and
  `postgres\bin\<name>.exe`. Windows unpacks with the `tar` and `flate2`
  crates (macOS keeps `/usr/bin/tar`), because the bundle has paths that
  `tar.exe` cannot write.
- `native/mod.rs`: on Windows the bundles go to `%LOCALAPPDATA%\deepx\engine`,
  not the app data folder (`%APPDATA%\deepx\deepx-desktop\data`). Python and
  Node need every path under 260 characters, and the bundle has paths of about
  150. Under the app data folder, a user name of 7 or more characters broke
  Python imports.
- `native/job.rs` and `native/process.rs`: each service runs in its own Job
  Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The process starts
  suspended and runs only after it is in its job, so no child escapes. A stop
  ends the job, and when the app ends or crashes, Windows ends the jobs too.
  So there is no leftover cleanup on Windows (`stop_leftovers` does nothing).
  Engine processes start with `CREATE_NO_WINDOW`, so no console window shows.
- `native/postgres.rs`: the clean stop of Postgres on Windows is
  `pg_ctl kill INT <pid>` (Postgres takes its signals that way on Windows).
  There is no `unix_socket_directories` on Windows. `initdb` and the server
  work in folders with spaces.
- `native/env.rs`: `PATH` is built with `std::env::join_paths`.

### 3. Hub (done)

`windows-x86_64` is in the `native.json` schema of `deepx-hub`, with tests,
and it was deployed on 2026-10-26. The hub is not deployed from git: run
`vercel deploy --prod` in the `deepx-hub` folder (check that the CLI shows the
`deepx-hub` project). Before that deploy, the schema refused unknown
platforms, so a `native.json` with a Windows entry would have made the hub
answer 502 for every platform, Macs included.

On Windows, run the hub's `pnpm install` and `pnpm test` with Node 22 or
later. With an older Node, pnpm skips rolldown's Windows binary and vitest
does not start.

### 4. Code runner (no sandbox-runtime on Windows) (done)

The code runner (`backend/onyx/deepx/code_runner/`) runs user code inside
Anthropic sandbox-runtime (`srt`). `srt` supports only macOS and Linux.
`sandbox.py` also uses `os.killpg` and process groups.

Do **not** run model-written code without a sandbox. On Windows the
`code_runner` service does not start (`HAS_CODE_RUNNER` in `native/env.rs`),
and `CODE_INTERPRETER_BASE_URL` is set to an **empty** string. Leaving it unset
is not enough: the backend then uses its default `http://localhost:8000`, and
the code tools show. Add a Windows sandbox later as its own task (for example
Windows Sandbox, a restricted token with a Job Object, or WSL with
bubblewrap).

### 5. Desktop app version

Raise the version in `desktop/src-tauri/Cargo.toml`,
`desktop/src-tauri/tauri.conf.json` and `desktop/package.json`. Use one
version for macOS and Windows.

## Test

1. Unit tests: `cd desktop/src-tauri` and run `cargo test`. They pass on
   Windows, with Windows cases for the paths, the `PATH` separator, the
   platform name, the unpacking and the job stop.
2. Build the bundle. Check that the app's unpacking reads it:
   `$env:DEEPX_TEST_ARCHIVE="<archive>"; $env:DEEPX_TEST_TARGET="<folder>";
   cargo test unpacks_a_real_bundle -- --ignored`. Then set
   `DEEPX_ENGINE_BUNDLE=<archive>` before `bun run tauri dev`, so that the app
   installs the local archive (`install_local_archive` in `bundle.rs`) instead
   of asking the hub.
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

1. Check that the deployed hub accepts `windows-x86_64` (step 3 of **What to
   change**). Build the Windows engine bundle and publish it in a new
   `native-v<ver>` release. Its `native.json` must keep the `darwin-aarch64` and
   `darwin-x86_64` entries and add `windows-x86_64`. Copy the macOS bundles
   from the last `native-v*` release, or rebuild them on the Mac.
2. Build the desktop app on Windows. Set the version in the working copy only
   (`Cargo.toml`, `tauri.conf.json`, `package.json`, as the release workflow
   does), run `bun run tauri build --bundles nsis,msi`, and then restore those
   files and `Cargo.lock`. Without the signing key, leave
   `createUpdaterArtifacts` off and upload the installers to a draft
   `desktop-v<ver>`. The Mac then signs the `-setup.exe` with
   `bun run tauri signer sign` (see `docs/DEEPX_MAC_CONTINUE.md`), so the key
   stays on the Mac. With the key on Windows, set
   `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` and
   turn `createUpdaterArtifacts` on instead.
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
