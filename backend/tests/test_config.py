"""config 预设迁移测试 —— 存量用户升级到「V4 思考支持工具 + Think Max」。"""
from __future__ import annotations

import config


def _old_deepseek_provider() -> dict:
    """模拟 v1.0.0 存量用户:思考预设带旧 supports_tools:False、无 Think Max。"""
    return {
        "id": "abc", "name": "DeepSeek", "format": "openai_compat",
        "base_url": "https://api.deepseek.com", "api_key": "sk-keep",
        "capability": "x", "presets": [
            {"label": "V4 Pro·普通", "model": "deepseek-v4-pro",
             "extra_body": {"thinking": {"type": "disabled"}}},
            {"label": "V4 Pro·思考", "model": "deepseek-v4-pro",
             "extra_body": {"thinking": {"type": "enabled"}},
             "supports_tools": False},
        ],
    }


def test_migrate_refreshes_old_deepseek_install():
    s = {"providers": [_old_deepseek_provider()]}
    assert config._migrate_deepseek_presets(s) is True
    p = s["providers"][0]
    labels = {ps["label"] for ps in p["presets"]}
    assert "V4 Pro·Think Max" in labels                  # 补上 Think Max
    think = next(ps for ps in p["presets"] if ps["label"] == "V4 Pro·思考")
    assert "supports_tools" not in think                 # 解除工具限制
    assert p["api_key"] == "sk-keep"                      # 保留用户 key
    # 幂等:再跑不再变更、不重复加 Think Max
    assert config._migrate_deepseek_presets(s) is False
    assert sum(ps["label"] == "V4 Pro·Think Max" for ps in p["presets"]) == 1


def test_migrate_skips_non_deepseek_and_custom_presets():
    custom = {"id": "z", "name": "X", "base_url": "https://api.other.com",
              "api_key": "k", "presets": [
                  {"label": "我的自定义", "model": "m", "extra_body": {}}]}
    s = {"providers": [custom]}
    assert config._migrate_deepseek_presets(s) is False  # 非 deepseek,不动
    assert s["providers"][0]["presets"][0]["label"] == "我的自定义"
