"""确认档 + 内存存储的测试。"""

from cogito_engine.confirm import RiskyConfirmPolicy
from cogito_engine.store import MemoryStore


def test_non_high_risk_never_confirms():
    for level in ("all", "risky", "none"):
        p = RiskyConfirmPolicy(level)
        assert p.needs_confirm("read_file", high_risk=False) is False


def test_level_none_never_confirms():
    p = RiskyConfirmPolicy("none")
    assert p.needs_confirm("delete_path", high_risk=True) is False


def test_level_all_confirms_every_high_risk():
    p = RiskyConfirmPolicy("all")
    assert p.needs_confirm("write_file", high_risk=True) is True
    assert p.needs_confirm("delete_path", high_risk=True) is True


def test_level_risky_only_irreversible():
    p = RiskyConfirmPolicy("risky")
    assert p.needs_confirm("delete_path", high_risk=True) is True   # 不可逆
    assert p.needs_confirm("run_command", high_risk=True) is True   # 跑命令
    assert p.needs_confirm("write_file", high_risk=True) is False   # 有 git 兜底,静默


def test_unknown_level_falls_back_to_risky():
    p = RiskyConfirmPolicy("bogus")
    assert p.level == "risky"


def test_memory_store_roundtrip():
    s = MemoryStore()
    assert s.load("x") is None
    s.save("x", {"a": 1})
    assert s.load("x") == {"a": 1}


def test_memory_store_isolates_copies():
    s = MemoryStore()
    data = {"n": 1}
    s.save("x", data)
    data["n"] = 999          # 外部改原 dict 不应影响已存的
    assert s.load("x") == {"n": 1}
