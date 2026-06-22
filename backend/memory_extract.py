"""自动记忆提炼 —— 让记忆"自建"与模型强弱无关。

主对话之外,每轮结束后(先过零成本启发式门控)跑一个**聚焦** LLM 调用:按
Claude 的记忆纪律,从这一轮对话里提炼值得长期记的事实 → 结构化 JSON → 后端
去重落库。强模型仍可用 memory_save 即时记;本步是让**任何**模型都能自动建
记忆的安全网。两轨喂同一个记忆库(靠 name + 已有索引去重)。

provider 无关:run() 接一个有 async tool_complete(messages, specs) 的 provider。
"""

from __future__ import annotations

import json
import re
from typing import Any

import memory

# 门控信号:用户表达偏好/决定/纠正/约定时,这一轮多半有可记的东西。
_SIGNALS = (
    "记住", "记一下", "以后", "别再", "不要", "我喜欢", "我偏好", "我的",
    "总是", "每次", "规则", "约定", "习惯", "改成", "默认", "下次",
    "remember", "always", "never", "prefer", "my ", "note that",
)


def worth_extracting(user_text: str, assistant_text: str,
                     used_tools: bool) -> bool:
    """零成本门控:明显的寒暄/简单问答跳过,省 token。拿不准就放行(提炼步
    自己会判断、没东西就返回空)。"""
    if used_tools:                       # 干了活/做了决定 → 值得看一眼
        return True
    u = (user_text or "").strip()
    if len(u) < 5:                       # 太短(你好/谢谢/嗯)→ 跳
        return False
    if any(s in u for s in _SIGNALS):    # 含偏好/约定信号 → 看
        return True
    return len(u) > 160                  # 较长的实质输入 → 看;否则跳


_SYS = (
    "你是 Cogito 的记忆提炼器。任务:从下面这一轮对话里,提炼出**值得长期"
    "记住**的事实,输出 JSON 数组。严格遵守:\n"
    "【只记持久且非显然的】用户是谁/偏好(user)、用户对工作方式的指导"
    "(feedback,正文带 Why+如何应用)、进行中的工作/目标/约束(project,相对"
    "日期转绝对)、外部资源指针(reference)。\n"
    "【绝不记】只跟本次对话相关的临时内容、代码结构/改过的 bug/git 历史"
    "(这些代码里有)、寒暄、一次性问答。\n"
    "【去重】下面给了『已有记忆索引』。若某事已在索引里:用它的 name 给出"
    "更新版(覆盖);若没新增价值就别输出它。不要造重复。\n"
    "【格式】只输出 JSON 数组,每项 {name(kebab-case 短 slug), description"
    "(一句话摘要,召回靠它), type(user|feedback|project|reference), body"
    "(事实正文)}。没有任何值得记的就输出 []。不要输出 JSON 以外的任何字。"
)


def build_messages(user_text: str, assistant_text: str,
                   index_text: str) -> list[dict[str, str]]:
    idx = index_text.strip() or "(空)"
    convo = (f"【已有记忆索引】\n{idx}\n\n"
             f"【这一轮对话】\n用户:{user_text.strip()[:2000]}\n\n"
             f"助手:{assistant_text.strip()[:2000]}\n\n"
             "请输出 JSON 数组(没有就 [])。")
    return [{"role": "system", "content": _SYS},
            {"role": "user", "content": convo}]


def parse_candidates(content: str) -> list[dict[str, str]]:
    """从模型回复里宽松抽出 JSON 数组(容忍思考前言/代码围栏)。"""
    if not content:
        return []
    m = re.search(r"\[.*\]", content, re.S)   # 第一个 [ ... ] 块
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    out: list[dict[str, str]] = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        desc = str(it.get("description") or "").strip()
        if not name or not desc:
            continue
        out.append({
            "name": name,
            "description": desc,
            "type": str(it.get("type") or "project").strip(),
            "body": str(it.get("body") or desc).strip(),
        })
    return out


def apply_candidates(root: str, cands: list[dict[str, str]]) -> list[str]:
    """落库(save_memory 按 name 覆盖=更新或新建)。返回写入的 name 列表。"""
    written: list[str] = []
    for c in cands[:8]:                  # 单轮最多写 8 条,防失控
        r = memory.save_memory(root, c["name"], c["description"],
                               c["type"], c["body"])
        if r.get("saved"):
            written.append(r["saved"])
    return written


async def run(root: str, user_text: str, assistant_text: str,
              provider: Any) -> dict[str, Any]:
    """完整提炼流程(调用方已过门控)。provider 出错/无候选都安全返回。"""
    index_text = memory.load_index(root)
    msgs = build_messages(user_text, assistant_text, index_text)
    try:
        resp = await provider.tool_complete(msgs, [])
    except Exception as e:               # noqa: BLE001 提炼失败绝不影响主流程
        return {"error": str(e), "written": []}
    cands = parse_candidates(resp.get("content") or "")
    return {"written": apply_candidates(root, cands), "count": len(cands)}
