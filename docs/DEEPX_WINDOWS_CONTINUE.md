# Continue on Windows: check engine 0.2.5 and desktop 0.1.9

This note is for the Windows computer (you, or a Claude session on Windows).
The Mac finished both releases on 2026-10-26. Read it on `main`.

**Status (2026-10-26, Windows):** the releases check out (both live, 3
platforms each; the Windows `-setup.exe` matches the local build and its
updater signature is valid). Steps 2 to 4 are **blocked**: Microsoft Defender
removes `deepx.exe` (`Trojan:Win32/Bearfoos.A!ml`) soon after the engine
starts. Engine 0.2.5 downloaded and its database and backend started before
that. See the blocker in **Current state** and **Code signing (Windows)** in
`docs/DEEPX_WINDOWS.md`.

**Update (2026-10-28):** step 2 is done (0.1.8 updated to 0.1.9, engine
0.2.5 runs, health 200), and Defender with new definitions no longer flags
0.1.9. A code job through the runner worked with real-time protection on.
Left: the live chat of step 3 (needs an account and the Route key, typed
into the app by the user) and step 4.

## What the Mac did

- **Engine 0.2.5 (`native-v0.2.5`) is live.** It has the 3 bundles
  (`darwin-aarch64`, `darwin-x86_64`, `windows-x86_64`) and one `native.json`
  with the 3 platforms. All bundles are from commit `db0744a44`.
- **Desktop 0.1.9 (`desktop-v0.1.9`) is live.** It has 8 files: `.dmg`,
  `.app.tar.gz` and its `.sig`, `-setup.exe` and its `.sig`, `.msi`,
  `latest.json` (3 platforms) and `downloads.json`.
- **The hub serves both.** `https://deepx-hub.vercel.app/api/engine/native`
  shows 0.2.5, `https://deepx-hub.vercel.app/api/desktop/latest.json` shows
  0.1.9, and the download page links the 0.1.9 `.dmg`, `-setup.exe` and
  `.msi`.
- **Tested on the Mac:**
  - The code runner tests passed against the 0.2.5 arm64 bundle: 20 passed,
    3 Windows-only tests skipped.
  - Desktop 0.1.9 is installed. The app downloaded engine 0.2.5 by itself,
    and `http://127.0.0.1:9999/api/health` returns 200.
  - A live chat with Dxr Pro used the Python code tool. It made `sales.xlsx`
    and a `sales.png` bar chart. The chart shows in the chat, and the Excel
    preview opens. The running timer and the model descriptions show
    correctly.
- **Changes on `main`:**
  - `127bd5aa1`: `COMSPEC`, `LOCALAPPDATA`, `PATHEXT` and `SYSTEMROOT` are
    in `backend/scripts/env_inventory_baseline.txt`. They are Windows system
    variables, and the env drift pre-commit hook failed without them.
  - `7722ea58c`: `test_code_runner.py` now parses `DEEPX_TEST_SRT_COMMAND`
    with `shlex.split`, the same as the runner config. On macOS, quote paths
    that have spaces. The docs now show the releases as done.

## What is left on Windows

### 1. Pull `main`

```powershell
git checkout main
git pull --ff-only origin main
```

### 2. Check the update from 0.1.8 to 0.1.9

On a Windows computer with desktop 0.1.8:

1. Start Deep X. The app must offer the 0.1.9 update. Install it.
2. After the restart, check that the app is 0.1.9 and downloads engine 0.2.5.
   The bundles are in `%LOCALAPPDATA%\deepx\engine`: a `0.2.5` folder must
   be there, and `current.json` must show `0.2.5`.
3. Open `http://localhost:9999/api/health`. It must return 200. The footer
   in the chat shows `Deep X 0.2.5`.

If the update does not show, check that the `windows-x86_64` entry in
`latest.json` has a `signature` and the `-setup.exe` URL.

### 3. Do a live chat with the code tool on Windows

The user allows a small amount of Deep X Route credit for this test. Use
**Dxr Pro**, and send one message:

> Use the Python code tool. Make a small table of monthly sales for Jan to
> Jun (any sample numbers), save it as sales.xlsx, and draw a bar chart of it
> as sales.png. Give me both files.

Check that:

- The header shows the running time while the answer runs.
- The chart shows in the chat, and `sales.xlsx` opens in the preview.
- `code_runner.log` in `%APPDATA%\deepx\deepx-desktop\data\engine-native\logs`
  has no errors.
- After you quit the app, no `python.exe` from the bundle's `jobpython\`
  stays running (check Task Manager).

Delete the test chat after the check if the user does not want to keep it.

### 4. Test a new installation on a clean Windows account

This is step 4 of **Test** in `docs/DEEPX_WINDOWS.md`, and it is not done
yet. Use a Windows user account with no Docker installed:

1. Download the `-setup.exe` from the download page and install it.
2. Setup has 2 steps (Deep X Route, then start), with no Docker step.
3. The engine starts, and sign-up, log-in and one chat work.

### 5. Finish

Write the results in **Current state** in `docs/DEEPX_WINDOWS.md`, and mark
this note done. If a check fails, fix it on a branch. A fix in the engine
needs a new `native-v*` release with all 3 bundles. A fix in the desktop
needs a new `desktop-v*` release, and the Mac signs the `-setup.exe` (see
**Release** in `docs/DEEPX_WINDOWS.md`). Commit, merge to `main` and push.

## Rules

- Never print, paste or commit secrets (signing key, API keys, `.env`
  files).
- Spend Deep X Route credits only on the one test chat in step 3.
- Kill only processes that you started. Do not use a broad `taskkill /IM`.
- Do not read `backend/ee/`, `web/src/ee/` or `web/src/app/ee/`.
- `native.json` and `latest.json` must always list all 3 platforms.
