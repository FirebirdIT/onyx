# Deep X Desktop

The Deep X desktop app. It runs the Deep X engine on this computer as normal
processes, with no Docker, and it opens the engine at `http://localhost:9999`
in a native window. It can also open a remote Deep X server. The engine runs on
macOS only for now (Apple silicon and Intel bundles). On other computers, use
a remote server.

Built with [Tauri](https://tauri.app) v2.

## What the app does

- **First-run setup** (`src/index.html`, `src/setup.js`):
  1. Connects Deep X Route: "Sign in with Deep X Route" (PKCE, only when
     `deepx_route_client_id` is set) or "paste API key". The app checks each
     key with `GET https://api.deepxroute.com/v1/key` and keeps it in the OS
     keychain (or in a 0600 file if there is no keychain).
  2. Starts the engine and shows the progress, then opens the app.
- **Engine** (`src-tauri/src/engine/native/`): downloads the engine bundle from
  `{hub_url}/api/engine/native` and checks its sha256, then starts Postgres,
  runs the migrations, and starts the code runner, the API server and the web
  server. A crashed service restarts. See `native-engine/build-bundle.sh` for
  the bundle.
- **Settings** (`⌘ ,`): start, stop, restart, "stop the engine when Deep X
  quits", Deep X Route key, and a remote server address.
- **Updates**: on start and from **Help > Check for Updates…**. The feed is
  `{hub_url}/api/desktop/latest.json`.
- **Backup and restore**: **File > Back Up Now…** and
  **File > Restore from Backup…**. The app encrypts a `pg_dump` of the engine
  database on this computer and uploads it to the hub in parts. The hub sees
  only ciphertext.

## Engine files

```
<app data>/
├── engine/secrets.json        # database password and auth secret, made once, mode 0600
└── engine-native/
    ├── bundles/<version>/     # the unpacked engine bundle, and current.json
    ├── postgres-data/         # the database
    ├── code-runner/           # code runner jobs and files (temporary)
    └── logs/                  # one log per service, and engine.log for start failures
```

Files stay in Postgres (`FILE_STORE_BACKEND=postgres`), so a backup holds the
user files. Versions before 0.1.4 ran the engine in Docker; a config that still
points to `http://localhost:3000` moves to `http://localhost:9999` when it loads.

App data folder:

- macOS: `~/Library/Application Support/com.deepx.deepx-desktop/`
- Windows: `%APPDATA%\deepx\deepx-desktop\data\`

## Configuration

The config file is `config.json` in the config folder:

- macOS: `~/Library/Application Support/com.deepx.deepx-desktop/config.json`
- Windows: `%APPDATA%\deepx\deepx-desktop\config\config.json`
- Linux: `~/.config/deepx-desktop/config.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `server_url` | `http://localhost:9999` | The address that the window opens. |
| `local_engine` | `true` | Start and stop the local engine. `false` for a remote server. |
| `stop_engine_on_quit` | `true` | Stop the engine when the app quits. |
| `setup_complete` | `false` | `true` after the first-run setup. |
| `hub_url` | `https://deepx-hub.vercel.app` | The hub for the engine bundle, updates and backups. |
| `deepx_route_client_id` | `""` | Public client ID for the Deep X Route sign-in. Empty shows only "paste API key". |

Press `⌘ ,` (Settings) to change most values. To use a remote server, enter its
address in Settings. Then the app does not start the local engine.

## Keyboard shortcuts

| Shortcut | Action           |
| -------- | ---------------- |
| `⌘ N`    | New Chat         |
| `⌘ ⇧ N`  | New Window       |
| `⌘ R`    | Reload           |
| `⌘ [`    | Go Back          |
| `⌘ ]`    | Go Forward       |
| `⌘ ,`    | Settings         |
| `⌘ W`    | Close Window     |
| `⌘ Q`    | Quit             |

## Prerequisites

1. **Rust** (latest stable): `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
2. **Bun** (1.3+): `curl -fsSL https://bun.sh/install | bash`
3. **Xcode Command Line Tools** (macOS): `xcode-select --install`

## Development

Dependencies for `desktop/` are in the root bun workspace. Install once at the
repo root:

```bash
bun install
```

Then, from `desktop/`:

```bash
bun run dev      # development mode
bun run debug    # development mode with DevTools and console capture
```

Tests and lints, from `desktop/src-tauri/`:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
```

To test the engine with a local bundle, set `DEEPX_ENGINE_BUNDLE` to the
`.tar.gz` that `native-engine/build-bundle.sh` makes. `TARGET_ARCH=x86_64` builds
the Intel bundle on an Apple silicon Mac (Rosetta must be installed). The Intel
bundle installs `native-engine/intel-overrides.txt` in place of the locked
`cryptography` and `pyOpenSSL`, because `cryptography` 49 and newer have no Intel
macOS wheels. The build fails if `uv` would compile a package that has native
code from source. Do not run the engine in
development while another program uses port 9999.

## Building

```bash
bun run build          # current architecture
bun run build:dmg      # macOS universal (add both rustup targets first)
bun run build:windows  # Windows from macOS or Linux (cargo-xwin)
```

## Releases

Push a tag `desktop-v<semver>`, or run the **Deep X Desktop Release** workflow
(`.github/workflows/deepx-desktop-release.yml`). The workflow builds the macOS
universal app and the Windows installers, signs the updater files with
`TAURI_SIGNING_PRIVATE_KEY`, and publishes a GitHub release with `latest.json`.
Apple signing runs only when the `APPLE_CERTIFICATE` secrets are set.

The updater public key is `plugins.updater.pubkey` in
`src-tauri/tauri.conf.json`.

## Icons

The source icon is `src-tauri/icons/icon.svg`, a copy of `web/public/deepx.svg`.
To make the icon files again:

```bash
bunx tauri icon src-tauri/icons/icon.svg
```

`src-tauri/icons/tray-icon.svg` is the monochrome menu bar icon.

## Security notes

- The bundled pages get the engine, key and backup commands
  (`src-tauri/capabilities/local.json`). Server pages, local or remote, get only
  window dragging, the menu bar toggle and debug logging
  (`src-tauri/capabilities/main.json`). `build.rs` lists the app commands, so a
  command without a permission is refused.
- The app never writes the Deep X Route key or the engine secrets to a log.
- The engine listens on `127.0.0.1` only.

## License

MIT
