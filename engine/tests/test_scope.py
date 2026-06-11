"""DirScope 护栏测试 —— 重点验证「临时授权」语义(用户点名才放行,不是非授权就硬拒)。"""

from cogito_engine.scope import DirScope


def test_inside_root_allowed(tmp_path):
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    assert scope.is_allowed(str(tmp_path / "a.txt"))
    assert scope.is_allowed(str(tmp_path / "sub" / "deep" / "b.txt"))


def test_outside_root_rejected(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    scope = DirScope(cwd=str(root), allowed_roots=[str(root)])
    assert not scope.is_allowed(str(outside / "secret.txt"))


def test_directory_traversal_rejected(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    scope = DirScope(cwd=str(root), allowed_roots=[str(root)])
    # ..\ 穿越到父目录必须被拒(resolve 后落在 root 之外)
    assert not scope.is_allowed(str(root / ".." / "escape.txt"))


def test_temporary_grant_allows_outside(tmp_path):
    """核心修正:用户明确写出的目录,本轮临时放行——默认拒,点名后准。"""
    root = tmp_path / "proj"
    root.mkdir()
    outside = tmp_path / "userfiles"
    outside.mkdir()
    scope = DirScope(cwd=str(root), allowed_roots=[str(root)])

    assert not scope.is_allowed(str(outside / "x.txt"))      # 默认:拒
    scope.grant_temporary([str(outside)])                    # 用户点名放行
    assert scope.is_allowed(str(outside / "x.txt"))          # 现在:准
    assert scope.is_allowed(str(outside / "deep" / "y.txt"))  # 子路径也准


def test_temporary_grant_is_per_turn(tmp_path):
    """临时授权是覆盖式的:下一轮换一批,上一轮失效(防越授权残留)。"""
    root = tmp_path / "proj"
    root.mkdir()
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    scope = DirScope(cwd=str(root), allowed_roots=[str(root)])

    scope.grant_temporary([str(a)])
    assert scope.is_allowed(str(a / "x"))

    scope.grant_temporary([str(b)])          # 新的一轮
    assert scope.is_allowed(str(b / "x"))
    assert not scope.is_allowed(str(a / "x"))  # 上一轮的失效


def test_empty_path_rejected(tmp_path):
    scope = DirScope(cwd=str(tmp_path), allowed_roots=[str(tmp_path)])
    assert not scope.is_allowed("")
