/**
 * The cherrypick console as a desktop window.
 *
 * Deliberately a **window and nothing else**. The supervisor owns the console process; this shell
 * never spawns one, because two owners of :5070 means the supervisor's own child cannot bind and
 * every restart it attempts fails. When the port does not answer the shell says which of the four
 * causes it is (see status.ts) and keeps retrying.
 *
 * Because the server runs in its own Node process, `better-sqlite3` and `@napi-rs/keyring` are never
 * loaded inside Electron — so this needs no `electron-rebuild` step and no native module of its own.
 */
import { app, BrowserWindow, Menu, Tray, shell, nativeImage } from "electron";
import { consolePort, consoleUrl, BIND_HOST } from "@console/shared";
import { probeHealth, diagnose } from "./status.js";
import { loadBounds, saveBounds, type Bounds } from "./bounds.js";
import { splashDataUrl } from "./splash.js";

const RETRY_MS = 5000;

let win: BrowserWindow | null = null;
let tray: Tray | null = null;
let retryTimer: NodeJS.Timeout | null = null;
/** True once the real console has loaded, so a later blip shows the splash again rather than a
 *  browser error page. */
let showingConsole = false;

function url(): string {
  return consoleUrl();
}

async function render(): Promise<void> {
  if (win === null || win.isDestroyed()) return;
  const port = consolePort();
  if (await probeHealth(port)) {
    if (!showingConsole) {
      showingConsole = true;
      await win.loadURL(url());
    }
    stopRetrying();
    return;
  }
  showingConsole = false;
  await win.loadURL(splashDataUrl(diagnose(), url()));
  startRetrying();
}

function startRetrying(): void {
  if (retryTimer !== null) return;
  retryTimer = setInterval(() => void render(), RETRY_MS);
}

function stopRetrying(): void {
  if (retryTimer !== null) {
    clearInterval(retryTimer);
    retryTimer = null;
  }
}

function persistBounds(): void {
  if (win === null || win.isDestroyed()) return;
  const { x, y, width, height } = win.getNormalBounds();
  const bounds: Bounds = { x, y, width, height, maximized: win.isMaximized() };
  saveBounds(bounds);
}

function createWindow(): void {
  const saved = loadBounds();
  win = new BrowserWindow({
    ...saved,
    minWidth: 800,
    minHeight: 600,
    title: "cherrypick console",
    backgroundColor: "#0b0c0f", // the console's own background, so startup does not flash white
    autoHideMenuBar: true,
    webPreferences: {
      // The window shows a local page and nothing else; it needs no bridge into the main process,
      // so it gets none.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });
  if (saved.maximized === true) win.maximize();

  // Anything that is not the console opens in the real browser rather than inside this window —
  // the shell is not a general-purpose browser and should not become one.
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    void shell.openExternal(target);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, target) => {
    if (new URL(target).hostname !== BIND_HOST) {
      event.preventDefault();
      void shell.openExternal(target);
    }
  });

  // A load failure is the console going away mid-session; fall back to the splash, which explains it.
  win.webContents.on("did-fail-load", (_e, _code, _desc, failedUrl, isMainFrame) => {
    if (isMainFrame && !failedUrl.startsWith("data:")) {
      showingConsole = false;
      void render();
    }
  });

  win.on("close", persistBounds);
  win.on("closed", () => {
    win = null;
    stopRetrying();
  });

  void render();
}

function show(): void {
  if (win === null || win.isDestroyed()) {
    createWindow();
    return;
  }
  if (win.isMinimized()) win.restore();
  win.focus();
}

function buildMenu(): void {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: "Console",
        submenu: [
          { label: "Reload", accelerator: "CmdOrCtrl+R", click: () => void render() },
          {
            label: "Open in browser",
            click: () => void shell.openExternal(url()),
          },
          { type: "separator" },
          { role: "toggleDevTools" },
          { type: "separator" },
          { role: "quit" },
        ],
      },
      {
        label: "View",
        submenu: [
          { role: "resetZoom" },
          { role: "zoomIn" },
          { role: "zoomOut" },
          { type: "separator" },
          { role: "togglefullscreen" },
        ],
      },
    ]),
  );
}

function buildTray(): void {
  // An empty image rather than a bundled asset: packaging is deliberately deferred, and a tray with
  // no icon file is better than a startup crash on a missing one.
  tray = new Tray(nativeImage.createEmpty());
  tray.setToolTip("cherrypick console");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Open console", click: show },
      { label: "Open in browser", click: () => void shell.openExternal(url()) },
      { type: "separator" },
      { label: "Quit", click: () => app.quit() },
    ]),
  );
  tray.on("click", show);
}

// One window, always. A second launch focuses the existing one instead of opening a rival.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", show);

  void app.whenReady().then(() => {
    buildMenu();
    buildTray();
    createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });

  // Quitting the shell must never stop the console — the supervisor keeps it running, and that is
  // the whole point of the window-only design.
  app.on("window-all-closed", () => app.quit());
}
