// Custom title bar for Onyx Desktop
// This script injects a draggable title bar that matches Onyx design system

(function () {
  const TITLEBAR_ID = "onyx-desktop-titlebar";
  // Declared before init() runs (it can run at once, below).
  // The docked sidebar, or the rail and drawer of a narrow window.
  const SIDEBAR_SELECTOR =
    ".opal-sidebar-root__column, .opal-sidebar-root__overlay";
  const CONTENT_SELECTOR = ".bg-background";
  let paintScheduled = false;
  let observedSidebar = null;
  let sidebarResizeObserver = null;
  const TITLEBAR_HEIGHT = 36;
  const STYLE_ID = "onyx-desktop-titlebar-style";
  const VIEWPORT_VAR = "--onyx-desktop-viewport-height";

  // Wait for DOM to be ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  function getInvoke() {
    if (window.__TAURI__?.core?.invoke) return window.__TAURI__.core.invoke;
    if (window.__TAURI__?.invoke) return window.__TAURI__.invoke;
    if (window.__TAURI_INTERNALS__?.invoke)
      return window.__TAURI_INTERNALS__.invoke;
    return null;
  }

  async function startWindowDrag() {
    const invoke = getInvoke();

    if (invoke) {
      try {
        await invoke("start_drag_window");
        return;
      } catch (err) {}
    }

    const appWindow =
      window.__TAURI__?.window?.getCurrent?.() ??
      window.__TAURI__?.window?.appWindow;

    if (appWindow?.startDragging) {
      try {
        await appWindow.startDragging();
      } catch (err) {}
    }
  }

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = `
      :root {
        --onyx-desktop-titlebar-height: ${TITLEBAR_HEIGHT}px;
        --onyx-desktop-viewport-height: 100dvh;
        --onyx-desktop-safe-height: calc(var(--onyx-desktop-viewport-height) - var(--onyx-desktop-titlebar-height));
      }

      @supports not (height: 100dvh) {
        :root {
          --onyx-desktop-viewport-height: 100vh;
        }
      }

      /* Reserve the native overlay-titlebar strip on the ROOT element. Doing
         this on <html> (rather than <body>) keeps the reservation working no
         matter how the embedded web app structures or restyles <body>, and
         shifts every normal-flow descendant below the titlebar. box-sizing +
         padding-top carves out the strip; the fixed titlebar element is drawn
         on top of it. */
      html {
        box-sizing: border-box;
        height: var(--onyx-desktop-viewport-height) !important;
        padding-top: var(--onyx-desktop-titlebar-height) !important;
        overflow: hidden !important;
      }

      body {
        height: var(--onyx-desktop-safe-height) !important;
        min-height: 0 !important;
        margin: 0 !important;
        overflow: hidden !important;
      }

      /* Clamp full-viewport-height containers to the area BELOW the titlebar so
         their bottom edge doesn't overflow past the window. Covers raw Tailwind
         utilities AND the Opal layout classes that bake height:100vh into a
         compiled class name via @apply (which the utility overrides miss). */
      .h-screen,
      .max-h-screen,
      .opal-sidebar-root__column,
      .opal-sidebar-root__overlay[data-variant="mobile"],
      .opal-sidebar-root__overlay[data-variant="medium"] {
        height: var(--onyx-desktop-safe-height) !important;
        max-height: var(--onyx-desktop-safe-height) !important;
      }

      .min-h-screen {
        min-height: var(--onyx-desktop-safe-height) !important;
      }

      /* Fixed sidebar overlays / backdrops are positioned against the viewport,
         so inset-y-0 pins them to y=0 (under the traffic lights). Re-anchor them
         below the titlebar. */
      .opal-sidebar-root__overlay[data-variant="mobile"],
      .opal-sidebar-root__overlay[data-variant="medium"],
      .opal-sidebar-root__backdrop {
        top: var(--onyx-desktop-titlebar-height) !important;
        bottom: 0 !important;
      }

      #${TITLEBAR_ID} {
        cursor: default !important;
        -webkit-user-select: none !important;
        user-select: none !important;
        -webkit-app-region: drag;
        height: var(--onyx-desktop-titlebar-height);
      }
    `;
    document.head.appendChild(style);
  }

  // The strip above the page continues the page: the sidebar colour over the
  // sidebar and the content colour over the rest, with no shadow or rule, so
  // the window reads as one surface (the native unified title bar look).

  function backgroundOf(element) {
    if (!element) return null;
    const color = getComputedStyle(element).backgroundColor;
    return color && color !== "rgba(0, 0, 0, 0)" && color !== "transparent"
      ? color
      : null;
  }

  // The shown sidebar that reaches furthest right: the docked sidebar, or the
  // rail or open drawer of a narrow window.
  function findSidebar() {
    let widest = null;
    let widestRight = 0;
    for (const element of document.querySelectorAll(SIDEBAR_SELECTOR)) {
      const style = getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden") continue;
      const right = element.getBoundingClientRect().right;
      if (right > widestRight) {
        widest = element;
        widestRight = right;
      }
    }
    return widest;
  }

  function updateTitleBarTheme(isDark) {
    const titleBar = document.getElementById(TITLEBAR_ID);
    if (!titleBar) return;

    const content =
      backgroundOf(document.querySelector(CONTENT_SELECTOR)) ||
      backgroundOf(document.body) ||
      (isDark ? "rgb(18, 18, 18)" : "rgb(255, 255, 255)");
    const sidebar = findSidebar();
    const sidebarColor = backgroundOf(sidebar);
    const edge = sidebar ? Math.round(sidebar.getBoundingClientRect().right) : 0;

    titleBar.style.background =
      sidebarColor && edge > 0
        ? `linear-gradient(to right, ${sidebarColor} ${edge}px, ${content} ${edge}px)`
        : content;
    titleBar.style.borderBottom = "none";
    titleBar.style.boxShadow = "none";
  }

  function isDarkTheme() {
    return (
      document.documentElement.classList.contains("dark") ||
      Boolean(document.body?.classList.contains("dark"))
    );
  }

  // Repaint when the sidebar appears, folds or changes width.
  function schedulePaint() {
    if (paintScheduled) return;
    paintScheduled = true;
    requestAnimationFrame(() => {
      paintScheduled = false;
      updateTitleBarTheme(isDarkTheme());
    });
  }


  function watchSidebar() {
    if (!sidebarResizeObserver && typeof ResizeObserver === "function") {
      sidebarResizeObserver = new ResizeObserver(schedulePaint);
    }
    const sidebar = findSidebar();
    if (sidebar === observedSidebar) return;
    if (observedSidebar) sidebarResizeObserver?.unobserve(observedSidebar);
    observedSidebar = sidebar;
    if (sidebar) sidebarResizeObserver?.observe(sidebar);
    schedulePaint();
  }

  function buildTitleBar() {
    const titleBar = document.createElement("div");
    titleBar.id = TITLEBAR_ID;
    titleBar.setAttribute("data-tauri-drag-region", "");

    titleBar.addEventListener("mousedown", (e) => {
      // Only start drag on left click and not on buttons/inputs
      const nonDraggable = [
        "BUTTON",
        "INPUT",
        "TEXTAREA",
        "A",
        "SELECT",
        "OPTION",
      ];
      if (e.button === 0 && !nonDraggable.includes(e.target.tagName)) {
        e.preventDefault();
        startWindowDrag();
      }
    });

    // Apply initial styles matching current theme
    const htmlHasDark = document.documentElement.classList.contains("dark");
    const bodyHasDark = document.body?.classList.contains("dark");
    const isDark = htmlHasDark || bodyHasDark;

    // Apply styles matching Onyx design system with translucent glass effect
    titleBar.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      height: ${TITLEBAR_HEIGHT}px;
      background: transparent;
      z-index: 999999;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: default;
      user-select: none;
      -webkit-user-select: none;
      font-family: 'Hanken Grotesk', -apple-system, BlinkMacSystemFont, sans-serif;
      -webkit-app-region: drag;
      padding: 0 12px;
    `;

    // Apply correct theme
    updateTitleBarTheme(isDark);

    return titleBar;
  }

  function mountTitleBar() {
    if (!document.body) {
      return;
    }

    const existing = document.getElementById(TITLEBAR_ID);
    if (existing?.parentElement === document.body) {
      // Update theme on existing titlebar
      const htmlHasDark = document.documentElement.classList.contains("dark");
      const bodyHasDark = document.body?.classList.contains("dark");
      const isDark = htmlHasDark || bodyHasDark;
      updateTitleBarTheme(isDark);
      return;
    }

    if (existing) {
      existing.remove();
    }

    const titleBar = buildTitleBar();
    document.body.insertBefore(titleBar, document.body.firstChild);
    injectStyles();

    // Ensure theme is applied immediately after mount
    setTimeout(() => {
      const htmlHasDark = document.documentElement.classList.contains("dark");
      const bodyHasDark = document.body?.classList.contains("dark");
      const isDark = htmlHasDark || bodyHasDark;
      updateTitleBarTheme(isDark);
    }, 0);
  }

  function syncViewportHeight() {
    const viewportHeight =
      window.visualViewport?.height ??
      document.documentElement?.clientHeight ??
      window.innerHeight;

    if (viewportHeight) {
      document.documentElement.style.setProperty(
        VIEWPORT_VAR,
        `${viewportHeight}px`
      );
    }
  }

  function observeThemeChanges() {
    let lastKnownTheme = null;

    function checkAndUpdateTheme() {
      // Check both html and body for dark class (some apps use body)
      const htmlHasDark = document.documentElement.classList.contains("dark");
      const bodyHasDark = document.body?.classList.contains("dark");
      const isDark = htmlHasDark || bodyHasDark;

      if (lastKnownTheme !== isDark) {
        lastKnownTheme = isDark;
        updateTitleBarTheme(isDark);
      }
    }

    // Immediate check on setup
    checkAndUpdateTheme();

    // Watch for theme changes on the HTML element
    const themeObserver = new MutationObserver(() => {
      checkAndUpdateTheme();
    });

    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });

    // Also observe body if it exists
    if (document.body) {
      const bodyObserver = new MutationObserver(() => {
        checkAndUpdateTheme();
      });
      bodyObserver.observe(document.body, {
        attributes: true,
        attributeFilter: ["class"],
      });
    }

    // Also check periodically in case classList is manipulated directly
    // or the theme loads asynchronously after page load
    const intervalId = setInterval(() => {
      checkAndUpdateTheme();
    }, 300);

    // Clean up after 30 seconds once theme should be stable
    setTimeout(() => {
      clearInterval(intervalId);
      // But keep checking every 2 seconds for manual theme changes
      setInterval(() => {
        checkAndUpdateTheme();
      }, 2000);
    }, 30000);
  }

  function init() {
    mountTitleBar();
    syncViewportHeight();
    observeThemeChanges();

    window.addEventListener("resize", syncViewportHeight, { passive: true });
    window.visualViewport?.addEventListener("resize", syncViewportHeight, {
      passive: true,
    });

    window.addEventListener("resize", schedulePaint, { passive: true });
    watchSidebar();

    // Keep it around even if the app DOM re-renders, and follow the sidebar
    // across page changes.
    const observer = new MutationObserver(() => {
      if (!document.getElementById(TITLEBAR_ID)) {
        mountTitleBar();
      }
      watchSidebar();
    });

    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
    });

    // Fallback keep-alive check
    setInterval(() => {
      if (!document.getElementById(TITLEBAR_ID)) {
        mountTitleBar();
      }
    }, 1500);
  }
})();
