"""会话 KV 单测 — 契约:core/kv.py(#53 存储域)。

覆盖:set/get/delete/keys 生命周期、跨实例文件持久化、错误码(缺 key/超长值/
未知 op → EVT-100)、损坏文件 → PERS-221(不静默清空)。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from pyharness.core.kv import MAX_VALUE_CHARS, SessionKV
from pyharness.errors import PyHError


def _kv(tmp_path) -> SessionKV:
    return SessionKV(tmp_path / "s-abc12345.kv.json", session_id="s-abc12345")


def test_set_get_delete_keys(tmp_path):
    kv = _kv(tmp_path)
    assert "已写入 pref" in kv.apply_op("set", key="pref", value="dark")
    assert kv.apply_op("get", key="pref") == "dark"
    assert "已写入 theme" in kv.apply_op("set", key="theme", value="dark")
    keys = kv.apply_op("keys")
    assert "pref" in keys and "theme" in keys
    assert "已删除 pref" in kv.apply_op("delete", key="pref")
    assert kv.apply_op("get", key="pref") == "(无此键: pref)"


def test_persistence_across_instances(tmp_path):
    kv1 = _kv(tmp_path)
    kv1.apply_op("set", key="note", value="跨实例存活")
    kv2 = SessionKV(tmp_path / "s-abc12345.kv.json", session_id="s-abc12345")
    assert kv2.apply_op("get", key="note") == "跨实例存活"
    # 原子写产物:文件为合法 JSON(无残留临时文件)
    raw = json.loads((tmp_path / "s-abc12345.kv.json").read_text(encoding="utf-8"))
    assert raw == {"note": "跨实例存活"}


def test_empty_and_ops(tmp_path):
    kv = _kv(tmp_path)
    assert kv.apply_op("keys") == "(空)"
    assert "(无此键" in kv.apply_op("get", key="ghost")


def test_error_codes(tmp_path):
    kv = _kv(tmp_path)
    with pytest.raises(PyHError) as e1:
        kv.apply_op("frob")
    assert e1.value.code == "EVT-100"
    with pytest.raises(PyHError) as e2:
        kv.apply_op("get")
    assert e2.value.code == "EVT-100"
    with pytest.raises(PyHError) as e3:
        kv.apply_op("set", key="big", value="x" * (MAX_VALUE_CHARS + 1))
    assert e3.value.code == "EVT-100"


def test_corrupt_file_pers(tmp_path):
    p = tmp_path / "s-abc12345.kv.json"
    p.write_text("{ 坏 JSON !!", encoding="utf-8")
    kv = _kv(tmp_path)
    with pytest.raises(PyHError) as e:
        kv.apply_op("keys")
    assert e.value.code == "PERS-221"


def test_concurrent_sets_do_not_lose_updates(tmp_path):
    """线程池并发 set 必须串行化读改写,不能发生 lost update。"""
    kv = _kv(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda i: kv.apply_op("set", key=f"k{i}", value=str(i)),
            range(40)))
    keys = kv.apply_op("keys").split("、")
    assert len(keys) == 40
    assert all(kv.apply_op("get", key=f"k{i}") == str(i) for i in range(40))
