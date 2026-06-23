# cogito-engine

从零手写、**零框架依赖**的极简 AI agent 内核:agent 循环(ReAct + 高危确认暂停)+ 工具系统(作用域护栏)+ provider 抽象。

引擎只依赖协议(`Provider` / `Scope` / `ConfirmPolicy` / `SessionStore`),不绑定任何具体应用;自带通用默认实现(`DirScope` / `RiskyConfirmPolicy` / `MemoryStore`),开箱可用,关键处可注入。

## 安装

尚未发布到 PyPI,从本仓库以**可编辑**方式安装:

```bash
pip install -e engine        # 或在 engine/ 目录下:pip install -e .
```

## 核心导出

| 组 | 名字 |
|---|---|
| 协议(可换自己的实现) | `Provider` · `Scope` · `ConfirmPolicy` · `SessionStore` |
| 作用域护栏 | `DirScope`(限定授权根)· `AllowAllScope` |
| 高危确认策略 | `RiskyConfirmPolicy`(写/删/命令)· `RootConfirmPolicy`(仅根目录外确认) |
| 工具 / 会话 / 持久化 | `ToolRegistry` · `AgentSession` · `MemoryStore` |
| provider | `OpenAICompatProvider` · `StreamingProvider` · `get_provider` |

## 用法

```python
from cogito_engine import DirScope, RiskyConfirmPolicy, MemoryStore
# scope = DirScope(cwd, allowed_roots);  scope.grant_temporary([...]) 本轮临时放行
# provider / 工具 / 会话如何装配成一个完整应用,见 Cogito 后端 backend/agents.py
```

## 测试

```bash
pip install pytest
pytest engine/tests -q       # 全离线,用脚本化的假 provider
```

> 从 [Cogito](../README.md) 抽出的核心引擎。当前版本 0.1.0 · MIT License.
