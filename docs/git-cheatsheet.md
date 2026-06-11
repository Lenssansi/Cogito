# Git 速查 · Cogito 自用

> **心智模型一句话:** git 是一堆用哈希命名的**不可变快照(commit)**,分支和 `HEAD` 只是指向它们的**可移动指针**。所有命令,不是在"造快照"就是在"挪指针"。
>
> **三个区:** 工作区(你编辑的文件) ──`git add`──▶ 暂存区(下次提交装什么) ──`git commit`──▶ 仓库(`.git` 里的历史)。

---

## 日常循环(占 90%)

| 命令(别名) | 作用 |
|---|---|
| `git st`  (= `status -sb`) | 看改了/暂存了什么(`??`未跟踪 · `M`已改 · `A`已暂存) |
| `git add <文件>` / `git aa` (= `add -A`) | 把改动挑进暂存区 |
| `git diff` / `git diff --staged` | 看**未暂存** / **已暂存**的逐行改动 |
| `git cm "信息"`  (= `commit -m`) | 把暂存区封成一个快照 |
| `git lg`  (= `log --oneline --graph --decorate`) | 看历史 |

> 一次提交三步:**`git st`(看)→ `git aa`(暂存)→ `git cm "信息"`(提交)**。
> 嫌麻烦:`scripts\save.ps1 "信息"`(自动 add 全部 + 提交)。

## 翻历史 / 看旧版本

| 命令 | 作用 |
|---|---|
| `git lg` / `git graph`(含所有分支) | 历史图 |
| `git show <commit>` | 看某个提交的改动详情 |
| `git diff <旧commit> <新commit>` | 比两个版本的差异 |
| `git switch --detach <commit>` | **参观**某个旧快照(工作区变那版,新版本不受影响;`git switch main` 回来) |
| `git restore --source=<commit> <文件>` | 只把**某个文件**退回旧版 |

## 分支

| 命令 | 作用 |
|---|---|
| `git switch -c <名>` | 新建并切到分支 |
| `git switch <名>` | 切到已有分支(`git switch main` 回主线) |
| `git merge <名>` | 把某分支的活合并进当前分支 |
| `git branch` / `git branch -d <名>` | 看分支 / 删除已合并的分支 |

**何时开分支:** 大改 / 有风险 / 要协作 → 开分支;小而安全的增量(如加文档)→ 直接在 `main` 上做(solo 项目)。

## 合并冲突

同一处两边都改了,git 会在文件里插标记让你选:

```
<<<<<<< HEAD
你这边的版本
=======
对方分支的版本
>>>>>>> other-branch
```

删掉标记、留下想要的,然后 `git add <文件>` + `git commit` 收尾。冲突 = git 把选择权交还给你,不可怕。

## 撤销(按"丢不丢东西"从轻到重)

| 想干嘛 | 命令 | 原理 / 风险 |
|---|---|---|
| 丢弃某文件**未暂存**的改动 | `git restore <文件>` | 用上次提交覆盖工作区 |
| 撤销 `add`(改动留着) | `git unstage <文件>`  (= `restore --staged`) | 移出暂存区 |
| 改最后一个提交(漏文件 / 改信息) | `git commit --amend` | 替换最后一个提交 |
| 撤掉最后一次提交、改动留着待提交 | `git undo`  (= `reset --soft HEAD~1`) | 只挪分支指针 |
| 把分支**硬退回**某提交、丢掉后面的 | `git reset --hard <commit>` | ⚠️ 丢工作区改动(还能 `git reflog` 捞) |
| 撤销某个**旧**提交、保留完整历史 | `git revert <commit>` | 造一个"反做"的新提交(已分享的历史只能用它) |
| 后悔药的后悔药 | `git reflog` | 列出 HEAD 每次移动,误删的 commit 哈希在这找 |

## 远程(要上 GitHub 时,目前未配)

| 命令 | 作用 |
|---|---|
| `git remote add origin <url>` | 绑定远程(只需一次) |
| `git push -u origin main` | 把本地提交推上去(首次带 `-u`) |
| `git fetch` / `git pull` | 拉远程更新(`pull` = `fetch` + `merge`) |
| `git clone <url>` | 把远程整个拷到本地 |

> 本地提交 ≠ GitHub。没 `push` 之前,你的提交只在本机。

## 本项目约定

- **commit 信息:** 第一行简短祈使句(如 `加 README`),空行,再写"为什么 / 细节"。
- **commit 粒度:** 一个提交 = 一件逻辑上完整的事。
- **永不进库**(已在 `.gitignore`):`data/`(含密钥/令牌)、`.venv`、`node_modules`、`release/`。
- **换行符** 由 `.gitattributes` 统一为 LF(跨平台无 CRLF 噪音)。

## 别名一览

下表别名已配在**本机 git 全局**;换新电脑跑一遍 `scripts\git-aliases.ps1` 即可重装。

| 别名 | 等于 |
|---|---|
| `git st` | `status -sb` |
| `git aa` | `add -A` |
| `git cm "信息"` | `commit -m "信息"` |
| `git lg` | `log --oneline --graph --decorate` |
| `git graph` | `log --oneline --graph --decorate --all` |
| `git last` | `log -1 --stat`(看最后一个提交改了啥) |
| `git unstage <文件>` | `restore --staged <文件>` |
| `git undo` | `reset --soft HEAD~1`(撤最后一次提交,改动留着) |

---

> **一句话:** 虚拟机快照是"存档 / 读档";git 是"存档 / 读档 + 能从任意存档点分叉出平行世界,而且几乎不丢档"。
