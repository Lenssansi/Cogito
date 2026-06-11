# cogito-engine

从零手写、**零框架依赖**的极简 AI agent 内核:agent 循环(ReAct + 高危确认暂停)+ 工具系统(作用域护栏)+ provider 抽象。

引擎只依赖协议(`Provider` / `Scope` / `ConfirmPolicy` / `SessionStore`),不绑定任何具体应用;自带通用默认实现(`DirScope` / `RiskyConfirmPolicy` / `MemoryStore`),开箱可用,关键处可注入。

```python
from cogito_engine import DirScope, RiskyConfirmPolicy, MemoryStore
# scope = DirScope(cwd, allowed_roots);  scope.grant_temporary([...]) 本轮临时放行
```

> 从 [Cogito](../README.md) 抽出的核心引擎。MIT License.
