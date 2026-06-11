// Electron 主进程：唯一可见窗口。启动时隐藏拉起 后端 + Vite + Ollama，
// 就绪后开窗；退出时只杀「自己拉起的」。开发模式不变（源码即时改、
// Vite 热更新），但不再有任何 cmd 黑窗。
const {
  app,
  BrowserWindow,
  dialog,
  nativeTheme,
  ipcMain,
  session,
} = require("electron");
const path = require("path");
const http = require("http");
const fs = require("fs");
const crypto = require("crypto");
const { spawn } = require("child_process");

const PACKED = app.isPackaged;

// CSRF 防护:本进程随机生成一个 shell 令牌。
// - 启动后端时通过环境变量 AIH_SHELL_TOKEN 传给它
// - 给所有发往后端(127.0.0.1:8756)的请求注入 X-AIH-Shell 头
// 后端据此识别「请求确实来自 ai-helper 外壳」,而浏览器里的恶意网页
// 拿不到这个令牌、也无法经过本 session 注入 → 无法伪造。
const SHELL_TOKEN = crypto.randomBytes(24).toString("hex");
const ROOT = path.join(__dirname, "..", ".."); // dev: D:\ai-helper
const APP_DIR = path.join(ROOT, "app");
const BACKEND_DIR = path.join(ROOT, "backend");
const PY = path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe");
const VITE_JS = path.join(APP_DIR, "node_modules", "vite", "bin", "vite.js");
// 打包后：后端是 PyInstaller 冻结的单 exe，放在 resources/backend/
const BACKEND_EXE = process.resourcesPath
  ? path.join(process.resourcesPath, "backend", "ai-helper-backend.exe")
  : "";
const MODELS_DIR = path.join(ROOT, "ollama", "models");
const DEV_URL = "http://127.0.0.1:5173";

let ollamaProc = null;
let backendProc = null;
let viteProc = null;

function httpOk(url, timeout = 1500) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout }, (res) => {
      res.resume();
      resolve(res.statusCode > 0);
    });
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function waitUntil(fn, tries = 60, gap = 700) {
  for (let i = 0; i < tries; i++) {
    if (await fn()) return true;
    await new Promise((r) => setTimeout(r, gap));
  }
  return false;
}

async function ensureOllama() {
  if (await httpOk("http://127.0.0.1:11434/api/tags")) return;
  try {
    fs.mkdirSync(MODELS_DIR, { recursive: true });
  } catch {
    /* ignore */
  }
  try {
    ollamaProc = spawn("ollama", ["serve"], {
      shell: true,
      stdio: "ignore",
      windowsHide: true,
      env: { ...process.env, OLLAMA_MODELS: MODELS_DIR },
    });
    ollamaProc.on("error", () => (ollamaProc = null));
  } catch {
    ollamaProc = null;
  }
}

async function ensureBackend() {
  if (await httpOk("http://127.0.0.1:8756/api/health")) return;
  // 把 shell 令牌通过环境变量交给后端,供 CSRF 校验
  const backendEnv = { ...process.env, AIH_SHELL_TOKEN: SHELL_TOKEN };
  if (PACKED) {
    if (!fs.existsSync(BACKEND_EXE)) return;
    backendProc = spawn(BACKEND_EXE, [], {
      cwd: path.dirname(BACKEND_EXE),
      stdio: "ignore",
      windowsHide: true,
      env: backendEnv,
    });
  } else {
    if (!fs.existsSync(PY)) return; // 没装依赖：前端显示后端未连接
    backendProc = spawn(PY, ["main.py"], {
      cwd: BACKEND_DIR,
      stdio: "ignore",
      windowsHide: true,
      env: backendEnv,
    });
  }
  backendProc.on("error", () => (backendProc = null));
}

async function ensureVite() {
  if (PACKED) return; // 打包后用构建产物 dist，不需要 Vite
  if (await httpOk(DEV_URL)) return;
  if (!fs.existsSync(VITE_JS)) return;
  viteProc = spawn(process.execPath, [VITE_JS], {
    cwd: APP_DIR,
    stdio: "ignore",
    windowsHide: true,
    env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
  });
  viteProc.on("error", () => (viteProc = null));
}

function killOurs() {
  for (const p of [backendProc, viteProc, ollamaProc]) {
    if (p) {
      try {
        p.kill();
      } catch {
        /* ignore */
      }
    }
  }
  backendProc = viteProc = ollamaProc = null;
}

nativeTheme.themeSource = "system";
ipcMain.on("set-native-theme", (_e, t) => {
  if (t === "dark" || t === "light" || t === "system")
    nativeTheme.themeSource = t;
});

// 原生文件夹选择对话框（设置页「授权根目录」加白名单用）。
ipcMain.handle("dialog:pickFolder", async (e) => {
  const win =
    BrowserWindow.fromWebContents(e.sender) || BrowserWindow.getFocusedWindow();
  const r = await dialog.showOpenDialog(win, {
    title: "选择授权工作目录",
    properties: ["openDirectory", "createDirectory"],
  });
  return r.canceled ? "" : r.filePaths[0] || "";
});

// 读 settings.json 拿主题(开窗前),避免「先黑再切回亮」的闪烁
function readSavedTheme() {
  try {
    const candidates = PACKED
      ? [path.join(process.env.APPDATA || "", "ai-helper", "settings.json")]
      : [path.join(ROOT, "data", "settings.json")];
    for (const p of candidates) {
      if (p && fs.existsSync(p)) {
        const s = JSON.parse(fs.readFileSync(p, "utf-8"));
        const t = s.theme;
        if (t === "dark" || t === "light") return t;
        if (t === "system")
          return nativeTheme.shouldUseDarkColors ? "dark" : "light";
      }
    }
  } catch {
    /* ignore */
  }
  return "light"; // 与 DEFAULTS["theme"] 保持一致
}

function createWindow() {
  const savedTheme = readSavedTheme();
  const bg = savedTheme === "dark" ? "#0f1115" : "#ffffff";
  // 同步给 Windows 标题栏,防止边框颜色错位
  nativeTheme.themeSource = savedTheme;
  const win = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 900,
    minHeight: 600,
    title: "ai-helper",
    icon: path.join(ROOT, "assets", "icon.ico"),
    backgroundColor: bg,
    show: false, // 先建好再 show,避免空白白底闪一下
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.removeMenu();
  // 内容首帧就绪再 show:期间窗口是隐藏的,看不到默认背景闪烁
  win.once("ready-to-show", () => win.show());
  if (PACKED) {
    win.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  } else {
    win.loadURL(DEV_URL);
  }
}

app.whenReady().then(async () => {
  // 给所有发往本地后端的请求注入 shell 令牌(在拉起后端/开窗之前装好)
  session.defaultSession.webRequest.onBeforeSendHeaders(
    {
      urls: [
        "http://127.0.0.1:8756/*",
        "http://localhost:8756/*",
      ],
    },
    (details, cb) => {
      details.requestHeaders["X-AIH-Shell"] = SHELL_TOKEN;
      cb({ requestHeaders: details.requestHeaders });
    },
  );
  await Promise.all([ensureOllama(), ensureBackend(), ensureVite()]);
  // 开窗前必须同时等到后端 + (dev 模式下) Vite 就绪，否则窗口已开但
  // 后端还没起，前端会立刻报「后端未连接」。两个 wait 并行不串行。
  const waits = [
    waitUntil(() => httpOk("http://127.0.0.1:8756/api/health")),
  ];
  if (!PACKED) waits.push(waitUntil(() => httpOk(DEV_URL)));
  await Promise.all(waits);
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", killOurs);
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
