"""确认档 + 根纪律策略 + 内存存储的测试。"""

from cogito_engine.confirm import RiskyConfirmPolicy, RootConfirmPolicy
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


# ---------- RootConfirmPolicy:"根纪律"(C 模型:读自由,根外写确认) ----------

def test_root_policy_mutation_outside_always_confirms(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "o"
    outside.mkdir()
    # inner 是 none 档(全静默)也拦不住根外改动 —— 位置规则优先
    p = RootConfirmPolicy(str(root), inner=RiskyConfirmPolicy("none"))
    assert p.needs_confirm("write_file", True,
                           args={"path": str(outside / "a.txt")}) is True
    assert p.needs_confirm("delete_path", True,
                           args={"path": str(outside)}) is True


def test_root_policy_inside_defers_to_inner(tmp_path):
    p = RootConfirmPolicy(str(tmp_path), inner=RiskyConfirmPolicy("risky"))
    # 根内:risky 档下写静默(有 git 兜底)、删除仍确认(不可逆)
    assert p.needs_confirm("write_file", True,
                           args={"path": str(tmp_path / "a.txt")}) is False
    assert p.needs_confirm("delete_path", True,
                           args={"path": str(tmp_path / "a.txt")}) is True
    # 相对路径 = 根内
    assert p.needs_confirm("write_file", True,
                           args={"path": "rel.txt"}) is False


def test_root_policy_reads_not_location_gated(tmp_path):
    other = tmp_path / "o"
    other.mkdir()
    p = RootConfirmPolicy(str(tmp_path / "proj"),
                          inner=RiskyConfirmPolicy("risky"))
    # 读不受位置约束(C 模型:读自由)
    assert p.needs_confirm("read_file", False,
                           args={"path": str(other / "x.txt")}) is False


def test_root_policy_run_command_gated_by_cwd(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    out = tmp_path / "o"
    out.mkdir()
    p = RootConfirmPolicy(str(root), inner=RiskyConfirmPolicy("none"))
    assert p.needs_confirm("run_command", True,
                           args={"command": "x"}) is False        # 缺省=根内
    assert p.needs_confirm("run_command", True,
                           args={"command": "x", "cwd": str(out)}) is True


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
