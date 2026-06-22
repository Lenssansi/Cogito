# Cogito 开发文档

面向想读懂 / 本地跑起来 / 二次开发 Cogito 的开发者。产品介绍见根目录 [README](../README.md)。

> 一句话:Cogito = **手写的 agent 引擎**(`engine/`,可独立安装的库)+ **装配后端**(`backend/`,FastAPI)+ **桌面前端**(`app/`,React + Electron)。核心循环、工具系统、provider 抽象全部手写,不依赖任何 agent 框架。

---

## 目录结构

```
Cogito/
├─ engine/                  # 可安装的 agent 引擎库(cogito_engine),与本应用解耦
│  └─ cogito_engine/
│     ├─ types.py           #   协议:Provider / Scope / ConfirmPolicy / SessionStore
│     ├─ scope.py           #   作用域护栏:DirScope(白名单)/ AllowAllScope(全盘)
│     ├─ confirm.py         #   确认策略:RiskyConfirmPolicy / RootConfirmPolicy(根纪律)
│     ├─ tools.py           #   工具系统 ToolRegistry:fs / exec / git / todo,差异收进 Scope
│     ├─ session.py         #   AgentSession:ReAct 循环 + 两态高危确认 + 检查点 + context_hook
│     ├─ store.py           #   SessionStore 协议 + MemoryStore 实现
│     └─ providers/         #   provider 抽象(OpenAI 兼容:流式 + function-calling)
├─ backend/                 # 装配层:把引擎接到本应用(FastAPI + 配置 + 持久化 + 记忆)
│  ├─ main.py               #   FastAPI 入口:路由 + CORS + CSRF + 信任分级
│  ├─ agents.py             #   装配:Scope/Confirm/Store 注入、工具注册、SSE、记忆接线
│  ├─ memory.py             #   记忆机制:静态层(COGITO.md)+ 动态层(索引/召回/工具)
│  ├─ memory_extract.py     #   自动记忆提炼(每轮后台,与模型强弱无关地自建记忆)
│  ├─ config.py             #   配置 + 多 provider 数据模型(data/settings.json,gitignore)
│  ├─ security.py           #   信任分级(loopback=local / 远程=受限)+ 权限矩阵
│  ├─ llm.py provider 接线 / store.py 会话持久化 / search.py 联网 / skills_loader.py
├─ app/                     # 前端:Vite + React + TypeScript + Electron
│  ├─ src/                  #   pages(统一会话 ChatPage / 设置 / API 管理)+ components + api.ts
│  └─ electron/main.cjs     #   Electron 主进程:自举后端+Vite、动态端口、关窗整体退出
├─ docs/                    # 文档(本文件、git 速查表、LLM 原理十讲规划)
├─ assets/                  # 图标(make_icon.py 用代码生成)
├─ data/                    # 运行时配置 + 密钥(gitignore,绝不入库)
└─ start-dev.bat            # 一键开发启动(纯 ASCII)
```

---

## 本地跑起来

**环境**:Windows + Python 3.11 + Node 18+。

```bat
:: 首次安装(只跑一次)
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt   :: 含 -e ../engine 装引擎
cd app && npm install

:: 日常启动 —— 双击 start-dev.bat,或:
cd app && npm run dev
```

`npm run dev` = `electron .`:Electron 作为唯一进程主,**自举**后端(`backend/.venv` 跑 `main.py`)+ Vite,**开发端口运行时动态分配**(避免和别的项目撞口),就绪后开窗;关窗时 `taskkill /T` 连子进程一起杀,不留孤儿。

配 API:打开后在「设置 / API 管理」里加一个 OpenAI 兼容 provider(DeepSeek、本机 Ollama、或任意兼容端点)填 key。密钥只存在本机 `data/settings.json`。

---

## 测试

```bat
:: 引擎(纯离线,脚本化假 provider)
backend\.venv\Scripts\python -m pip install pytest
backend\.venv\Scripts\python -m pytest engine\tests -q

:: 后端(记忆 / CSRF / 提炼)
backend\.venv\Scripts\python -m pytest backend\tests -q

:: 前端类型检查
cd app && npx tsc --noEmit
```

---

## 打包(桌面安装包 / 免安装版)

产物输出到根目录 `release/`(gitignore,不入库)。需要先把后端冻结成单 exe(PyInstaller),再用 electron-builder 出 Windows 包:

```bat
:: 1) 冻结后端 → backend/dist/cogito-backend.exe(electron-builder 会作为 extraResources 打进去)
backend\.venv\Scripts\python -m PyInstaller backend\cogito-backend.spec

:: 2) 前端构建 + 出包(nsis 安装包 + 免安装 zip)
cd app && npm run dist
```

打包后 Electron 不再起 Vite,改加载 `app/dist`;后端用冻结的 exe;`data/` 写到 `%APPDATA%/Cogito`。

---

## 核心子系统

| 子系统 | 在哪 | 要点 |
|---|---|---|
| **Agent 循环** | `engine/cogito_engine/session.py` | ReAct 状态机;两态高危确认(断开式 respond 续跑 / on_confirm 单流);可取消;持久化(重启续跑) |
| **工具 + 护栏** | `engine/cogito_engine/tools.py` `scope.py` | 一套工具,边界差异全由注入的 Scope 决定;路径强制落在授权范围(防穿越) |
| **确认策略** | `engine/cogito_engine/confirm.py` | C 边界模型:读全盘自由,写/删/命令在根目录外永远确认 |
| **检查点/回滚** | `tools.py` + `session.py` | 懒检查点(纯聊天不打、一轮第一次改动前打一个);回滚 `reset --hard` + `clean -fd`(保 gitignore) |
| **provider** | `engine/cogito_engine/providers/` | 统一 OpenAI 兼容;流式逐 token + 思考流;function-calling |
| **记忆机制** | `backend/memory.py` `memory_extract.py` | 静态层(分层 COGITO.md,拼接进系统提示)+ 动态层(每项目记忆库 + 召回 + 工具)+ 每轮自动提炼 |
| **信任/安全** | `backend/security.py` `main.py` | loopback=local 全功能 / 远程受限;CSRF(shell 令牌 + loopback 源);后端强制 |

记忆机制详见 `backend/memory.py` 顶部 docstring(对照 Claude Code 的两层记忆 1:1 复刻)。

---

## 约定

- **可执行脚本(.bat/.ps1)一律 ASCII**,中文只放注释/UI/文档(cmd 的 GBK 代码页会把脚本里的中文读成乱码)。
- **本地服务端口不写死**,运行时动态抓空闲口。
- `data/`、`1.txt`、`COGITO.local.md` 含密钥/私人内容,**永不入库**(已 gitignore)。
- 引擎(`engine/`)对应用零依赖:对配置/持久化/搜索靠**依赖注入**,可被任意宿主复用。
