// First-run setup, engine start and settings for the Deep X desktop app.
(function () {
  const { invoke } = window.__TAURI__.core;
  const { listen } = window.__TAURI__.event;

  const $ = (id) => document.getElementById(id);
  let bootstrap = null;
  // "setup" during the first run; "settings" when the settings page opened
  // the Deep X Route step.
  let routeReturn = "setup";

  function applySystemTheme() {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const update = (e) =>
      document.documentElement.classList.toggle("dark", e.matches);
    update(query);
    query.addEventListener("change", update);
  }

  // `step` is the first-run progress ("Step 1 of 2"), or empty.
  function show(view, step) {
    document
      .querySelectorAll(".view")
      .forEach((el) => el.classList.toggle("active", el.id === `view-${view}`));
    $("stepText").textContent = step || "";
  }

  function setMessage(id, text, kind) {
    const el = $(id);
    el.textContent = text || "";
    el.className = `message${kind ? ` ${kind}` : ""}`;
  }

  function errorText(error) {
    return typeof error === "string" ? error : String(error);
  }

  function openApp() {
    window.location.href = bootstrap.server_url;
  }

  // ---- First run -------------------------------------------------------
  function beginSetup() {
    if (!bootstrap.engine_supported) {
      return showSettings(
        "The Deep X engine does not run on this computer yet. Enter the address of a Deep X server."
      );
    }
    return showRoute("setup");
  }

  // ---- Step 1: Deep X Route --------------------------------------------
  async function showRoute(returnTo) {
    routeReturn = returnTo;
    $("routeSkipBtn").textContent =
      returnTo === "setup" ? "Skip for now" : "Back";
    $("keyInput").value = "";
    setMessage("routeMessage", "");
    const status = await invoke("route_key_status");
    $("signInBlock").style.display = status.sign_in_available ? "" : "none";
    // Sign-in is the main action when it is there; the key is the fallback.
    $("keySaveBtn").className = status.sign_in_available ? "block" : "primary block";
    if (status.saved && returnTo === "setup") {
      setMessage("routeMessage", "A key is saved already.", "ok");
    }
    show("route", returnTo === "setup" ? "Step 1 of 2" : "");
  }

  async function afterKeySaved(text) {
    setMessage("routeMessage", text, "ok");
    if (routeReturn === "setup") {
      setTimeout(startEngine, 600);
    } else {
      await showSettings(
        "The key is saved. Restart the engine to use the new key."
      );
    }
  }

  async function saveKey() {
    const key = $("keyInput").value.trim();
    if (!key) {
      setMessage("routeMessage", "Paste your API key.", "error");
      return;
    }
    $("keySaveBtn").disabled = true;
    setMessage("routeMessage", "Checking the key");
    try {
      const location = await invoke("route_save_key", { key });
      $("keyInput").value = "";
      const where =
        location === "keychain" ? "the system keychain" : "a private file";
      await afterKeySaved(`The key is valid and saved in ${where}.`);
    } catch (error) {
      setMessage("routeMessage", errorText(error), "error");
    } finally {
      $("keySaveBtn").disabled = false;
    }
  }

  const SIGN_IN_TEXT = {
    connected: "Connected.",
    cancelled: "Sign-in was cancelled. No key was made.",
    expired: "Sign-in took too long. Try again.",
    uncertain:
      "The result is not known. Check the API keys in the Deep X Route console, remove the app key if you see one, and try again.",
  };

  async function signIn() {
    $("signInBtn").disabled = true;
    $("signInCancelBtn").style.display = "";
    setMessage("routeMessage", "Finish the sign-in in your browser.");
    try {
      const status = await invoke("route_sign_in");
      if (status === "connected") {
        await afterKeySaved("Connected. The key is saved on this computer.");
      } else {
        setMessage("routeMessage", SIGN_IN_TEXT[status] || status, "error");
      }
    } catch (error) {
      setMessage("routeMessage", errorText(error), "error");
    } finally {
      $("signInBtn").disabled = false;
      $("signInCancelBtn").style.display = "none";
    }
  }

  // ---- Step 2: Start the engine ------------------------------------------
  function appendLog(line) {
    const log = $("startLog");
    log.textContent += `${line}\n`;
    log.scrollTop = log.scrollHeight;
  }

  async function startEngine() {
    const firstRun = !bootstrap.setup_complete;
    $("startHint").style.display = firstRun ? "" : "none";
    $("startBusy").style.display = "";
    $("startRetryBtn").style.display = "none";
    $("startSettingsBtn").style.display = "none";
    $("startLog").textContent = "";
    setMessage("startMessage", "");
    show("start", firstRun ? "Step 2 of 2" : "");
    try {
      await invoke("engine_start");
      if (firstRun) {
        await invoke("complete_setup");
        bootstrap.setup_complete = true;
      }
      setMessage("startMessage", "The engine is ready.", "ok");
      openApp();
    } catch (error) {
      $("startBusy").style.display = "none";
      setMessage("startMessage", errorText(error), "error");
      $("startRetryBtn").style.display = "";
      $("startSettingsBtn").style.display = "";
    }
  }

  // ---- Settings -----------------------------------------------------------
  async function refreshStatus() {
    try {
      const status = await invoke("engine_status");
      $("engineStatus").textContent = status.running ? "Running" : "Stopped";
    } catch {
      $("engineStatus").textContent = "Not started";
    }
    const key = await invoke("route_key_status");
    $("keyStatus").textContent = key.saved ? "Saved" : "Not connected";
  }

  async function showSettings(message) {
    bootstrap = await invoke("get_bootstrap_state");
    $("stopOnQuit").checked = bootstrap.stop_engine_on_quit;
    $("serverInput").value = bootstrap.local_engine ? "" : bootstrap.server_url;
    $("serverHint").textContent = bootstrap.local_engine
      ? "Deep X uses the engine on this computer. To use a remote Deep X server, enter its address."
      : `Deep X uses the server at ${bootstrap.server_url}.`;
    setMessage("settingsMessage", message || "", message ? "ok" : "");
    $("settingsVersion").textContent = `Version ${bootstrap.app_version}`;
    show("settings");
    await refreshStatus();
  }

  async function runEngineAction(command, label) {
    setMessage("settingsMessage", `${label}…`);
    document
      .querySelectorAll("#view-settings button")
      .forEach((b) => (b.disabled = true));
    try {
      await invoke(command);
      setMessage("settingsMessage", `${label}: done.`, "ok");
    } catch (error) {
      setMessage("settingsMessage", errorText(error), "error");
    } finally {
      document
        .querySelectorAll("#view-settings button")
        .forEach((b) => (b.disabled = false));
      await refreshStatus();
    }
  }

  async function saveServer() {
    const url = $("serverInput").value.trim();
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
    } catch {
      setMessage(
        "settingsMessage",
        "Enter an address that starts with http:// or https://",
        "error"
      );
      return;
    }
    try {
      const saved = await invoke("set_server_url", { url });
      window.location.href = saved;
    } catch (error) {
      setMessage("settingsMessage", errorText(error), "error");
    }
  }

  // ---- Startup ------------------------------------------------------------
  async function openRemote() {
    show("boot");
    $("bootText").textContent = `Connecting to ${bootstrap.server_url}`;
    try {
      await invoke("check_server_reachable");
      openApp();
    } catch {
      await showSettings();
      setMessage(
        "settingsMessage",
        `Could not connect to ${bootstrap.server_url}. Check the address and your network.`,
        "error"
      );
    }
  }

  async function init() {
    applySystemTheme();
    await listen("engine-progress", (event) => appendLog(event.payload.line));
    show("boot");
    try {
      bootstrap = await invoke("get_bootstrap_state");
    } catch (error) {
      $("startBusy").style.display = "none";
      setMessage("startMessage", errorText(error), "error");
      show("start");
      return;
    }
    if (window.location.hash === "#settings") return showSettings();
    if (!bootstrap.local_engine) return openRemote();
    if (!bootstrap.setup_complete) return beginSetup();
    return startEngine();
  }

  function bind() {
    $("keySaveBtn").addEventListener("click", saveKey);
    $("keyInput").addEventListener("keydown", (e) => {
      if (e.key === "Enter") saveKey();
    });
    $("signInBtn").addEventListener("click", signIn);
    $("signInCancelBtn").addEventListener("click", () =>
      invoke("route_sign_in_cancel")
    );
    $("routeSkipBtn").addEventListener("click", () =>
      routeReturn === "setup" ? startEngine() : showSettings()
    );
    $("startRetryBtn").addEventListener("click", startEngine);
    $("startSettingsBtn").addEventListener("click", () => showSettings());
    $("stopOnQuit").addEventListener("change", (e) =>
      invoke("engine_set_stop_on_quit", { enabled: e.target.checked })
    );
    $("engineStartBtn").addEventListener("click", () =>
      runEngineAction("engine_start", "Starting the engine")
    );
    $("engineStopBtn").addEventListener("click", () =>
      runEngineAction("engine_stop", "Stopping the engine")
    );
    $("engineRestartBtn").addEventListener("click", () =>
      runEngineAction("engine_restart", "Restarting the engine")
    );
    $("reconnectBtn").addEventListener("click", () => showRoute("settings"));
    $("forgetKeyBtn").addEventListener("click", async () => {
      await invoke("route_forget_key");
      await refreshStatus();
    });
    $("serverSaveBtn").addEventListener("click", saveServer);
    $("useLocalBtn").addEventListener("click", async () => {
      await invoke("use_local_engine");
      bootstrap = await invoke("get_bootstrap_state");
      if (bootstrap.setup_complete) return startEngine();
      return beginSetup();
    });
    $("openAppBtn").addEventListener("click", async () => {
      bootstrap = await invoke("get_bootstrap_state");
      if (!bootstrap.local_engine) return openRemote();
      if (!bootstrap.setup_complete) return beginSetup();
      return startEngine();
    });
  }

  bind();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
