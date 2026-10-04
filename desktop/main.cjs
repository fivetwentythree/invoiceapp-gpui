const { app, BrowserWindow, Menu, dialog, shell } = require("electron");
const { spawn } = require("node:child_process");
const { randomBytes } = require("node:crypto");
const { existsSync, mkdirSync } = require("node:fs");
const path = require("node:path");

app.setName("Domestica Invoice");

let backendProcess = null;
let backendBaseUrl = "";
let mainWindow = null;

function appRoot() {
  return app.getAppPath();
}

function ensureDirectory(dir) {
  mkdirSync(dir, { recursive: true });
}

function backendCommand() {
  if (app.isPackaged) {
    const executable = process.platform === "win32" ? "domestica-backend.exe" : "domestica-backend";
    return {
      command: path.join(process.resourcesPath, "backend", executable),
      args: [],
    };
  }
  return {
    command: "uv",
    args: ["run", "python", "-m", "clean_ledger.web"],
  };
}

function desktopEnv(token) {
  const userData = app.getPath("userData");
  const dataDir = path.join(userData, "data");
  const filesDir = path.join(userData, "files");
  const emailDir = path.join(userData, "email_outbox");
  const fontDir = path.join(appRoot(), "node_modules", "@fontsource", "inter", "files");

  ensureDirectory(dataDir);
  ensureDirectory(filesDir);
  ensureDirectory(emailDir);

  const env = {
    ...process.env,
    CLEAN_LEDGER_DATA_DIR: dataDir,
    CLEAN_LEDGER_FILES_DIR: filesDir,
    CLEAN_LEDGER_EMAIL_DIR: emailDir,
    CLEAN_LEDGER_DB: path.join(dataDir, "clean_ledger.sqlite3"),
    CLEAN_LEDGER_PDF_RENDERER: path.join(appRoot(), "scripts", "render-invoice-pdf.mjs"),
    DOMESTICA_INTER_FONT_DIR: fontDir,
    DOMESTICA_DESKTOP_TOKEN: token,
  };

  if (app.isPackaged) {
    env.CLEAN_LEDGER_NODE_BINARY = process.execPath;
    env.CLEAN_LEDGER_ELECTRON_RUN_AS_NODE = "1";
  }

  return env;
}

function parseReadyLine(line) {
  try {
    const payload = JSON.parse(line);
    if (payload.event === "ready" && payload.url) {
      return payload.url;
    }
  } catch {
    return "";
  }
  return "";
}

function startBackend(token) {
  return new Promise((resolve, reject) => {
    const staticDir = path.join(appRoot(), "dist");
    if (!existsSync(path.join(staticDir, "index.html"))) {
      reject(new Error("The built dashboard was not found. Run npm run build first."));
      return;
    }

    const backend = backendCommand();
    const args = [
      ...backend.args,
      "--host",
      "127.0.0.1",
      "--port",
      "0",
      "--desktop-static-dir",
      staticDir,
      "--desktop-token",
      token,
    ];
    backendProcess = spawn(backend.command, args, {
      cwd: appRoot(),
      env: desktopEnv(token),
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });

    let stdout = "";
    let settled = false;
    const timeout = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error("Timed out waiting for Domestica Invoice backend to start."));
    }, 20000);

    backendProcess.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
      const lines = stdout.split(/\r?\n/);
      stdout = lines.pop() || "";
      for (const line of lines) {
        const url = parseReadyLine(line.trim());
        if (url && !settled) {
          settled = true;
          clearTimeout(timeout);
          resolve(url);
          return;
        }
        if (line.trim()) console.log(line);
      }
    });

    backendProcess.stderr.on("data", (chunk) => {
      console.error(chunk.toString("utf8"));
    });

    backendProcess.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(error);
    });

    backendProcess.on("exit", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(new Error(`Domestica Invoice backend exited before startup with code ${code}.`));
    });
  });
}

function buildMenu() {
  const template = [
    {
      label: "Domestica Invoice",
      submenu: [
        { role: "about" },
        { type: "separator" },
        {
          label: "Open Data Folder",
          click: () => shell.openPath(app.getPath("userData")),
        },
        { type: "separator" },
        { role: process.platform === "darwin" ? "hide" : "minimize" },
        { role: "quit" },
      ],
    },
    {
      label: "File",
      submenu: [
        {
          label: "Print",
          accelerator: "CmdOrCtrl+P",
          click: (_menuItem, browserWindow) => {
            const target = browserWindow || BrowserWindow.getFocusedWindow();
            if (target) {
              target.webContents.print();
            }
          },
        },
        { type: "separator" },
        { role: "close" },
      ],
    },
    {
      label: "Edit",
      submenu: [
        { role: "undo" },
        { role: "redo" },
        { type: "separator" },
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { role: "pasteAndMatchStyle" },
        { role: "delete" },
        { type: "separator" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow(token) {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 1080,
    minHeight: 720,
    title: "Domestica Invoice",
    backgroundColor: "#f8fafc",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (backendBaseUrl && url.startsWith(backendBaseUrl)) {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
          },
        },
      };
    }
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.loadURL(`${backendBaseUrl}/?desktop_token=${encodeURIComponent(token)}#jobs`);
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) {
    backendProcess.kill();
  }
  backendProcess = null;
}

const singleInstanceLock = app.requestSingleInstanceLock();
if (!singleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
  });
}

app.whenReady().then(async () => {
  const token = randomBytes(32).toString("hex");
  buildMenu();
  try {
    backendBaseUrl = await startBackend(token);
    createWindow(token);
  } catch (error) {
    dialog.showErrorBox(
      "Domestica Invoice could not start",
      error instanceof Error ? error.message : String(error)
    );
    app.quit();
  }
});

app.on("before-quit", stopBackend);

app.on("window-all-closed", () => {
  app.quit();
});
