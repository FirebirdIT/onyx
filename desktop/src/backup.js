// "Back up now" and "Restore from backup" window.
(function () {
  const { invoke } = window.__TAURI__.core;
  const { listen } = window.__TAURI__.event;
  const $ = (id) => document.getElementById(id);
  const restore =
    new URLSearchParams(window.location.search).get("mode") === "restore";

  const TEXT = restore
    ? {
        title: "Restore Deep X",
        intro:
          "Enter the email and the passphrase of your backup. The restore replaces all data in the engine on this computer.",
        button: "Restore",
        confirm:
          "The restore replaces all chats, files and settings in the engine on this computer. Continue?",
      }
    : {
        title: "Back up Deep X",
        intro:
          "The app encrypts the engine data on this computer before the upload. Only your email and passphrase can open the backup. If you lose the passphrase, nobody can restore the backup. A new backup replaces the one before it.",
        button: "Back up now",
        confirm: null,
      };

  function applySystemTheme() {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const update = (e) =>
      document.documentElement.classList.toggle("dark", e.matches);
    update(query);
    query.addEventListener("change", update);
  }

  function setMessage(text, kind) {
    $("message").textContent = text || "";
    $("message").className = `message${kind ? ` ${kind}` : ""}`;
  }

  function appendLog(line) {
    const log = $("log");
    log.style.display = "";
    log.textContent += `${line}\n`;
    log.scrollTop = log.scrollHeight;
  }

  async function submit(event) {
    event.preventDefault();
    const email = $("email").value.trim();
    const passphrase = $("passphrase").value;
    if (!restore && passphrase !== $("confirm").value) {
      setMessage("The two passphrases are not the same.", "error");
      return;
    }
    if (TEXT.confirm && !window.confirm(TEXT.confirm)) return;

    $("submitBtn").disabled = true;
    $("closeBtn").disabled = true;
    $("log").textContent = "";
    setMessage(restore ? "Restoring" : "Backing up");
    try {
      if (restore) {
        await invoke("backup_restore", { email, passphrase });
        setMessage(
          "The restore is complete. Reload the Deep X window.",
          "ok"
        );
      } else {
        const result = await invoke("backup_now", { email, passphrase });
        const mb = (result.size / (1024 * 1024)).toFixed(1);
        setMessage(`The backup is complete (${mb} MB).`, "ok");
      }
      $("passphrase").value = "";
      $("confirm").value = "";
    } catch (error) {
      setMessage(typeof error === "string" ? error : String(error), "error");
    } finally {
      $("submitBtn").disabled = false;
      $("closeBtn").disabled = false;
    }
  }

  async function init() {
    applySystemTheme();
    document.title = TEXT.title;
    $("title").textContent = TEXT.title;
    $("intro").textContent = TEXT.intro;
    $("submitBtn").textContent = TEXT.button;
    $("confirm").style.display = restore ? "none" : "";
    $("confirm").required = !restore;
    $("passphrase").autocomplete = restore ? "current-password" : "new-password";
    $("form").addEventListener("submit", submit);
    $("closeBtn").addEventListener("click", () => window.close());
    await listen("backup-progress", (event) => appendLog(event.payload));
  }

  init();
})();
