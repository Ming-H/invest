"""wencai 缓存/计数/离线哨兵 单测。"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import wencai


class TestWencai(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        wencai.set_cache_dir(self.dir)
        wencai.set_offline(False)
        c = wencai.counter()
        self._api0 = c["api"]
        self._hit0 = c["cache_hit"]

    def tearDown(self):
        wencai.set_cache_dir(None)
        wencai.set_offline(False)

    def _fake_cli(self, payload):
        def fake_run(cmd, **kw):
            class P:
                stdout = json.dumps(payload, ensure_ascii=False)
            return P()
        return fake_run

    def test_first_call_hits_api_and_writes_cache(self):
        payload = {"datas": [{"股票简称": "若羽臣"}]}
        with mock.patch.object(wencai.subprocess, "run", self._fake_cli(payload)):
            got = wencai.iwencai("测试 查询", limit=10)
        self.assertEqual(got[0]["股票简称"], "若羽臣")
        self.assertEqual(wencai.counter()["api"], self._api0 + 1)
        self.assertEqual(os.listdir(self.dir).__len__(), 1)

    def test_second_call_uses_cache_no_api(self):
        payload = {"datas": [{"股票简称": "若羽臣"}]}
        with mock.patch.object(wencai.subprocess, "run", self._fake_cli(payload)):
            wencai.iwencai("测试 查询", limit=10)
        # 第二次:即使 subprocess 坏了也应走缓存
        with mock.patch.object(wencai.subprocess, "run",
                              side_effect=RuntimeError("不应被调用")):
            got = wencai.iwencai("测试 查询", limit=10)
        self.assertEqual(got[0]["股票简称"], "若羽臣")
        self.assertEqual(wencai.counter()["api"], self._api0 + 1)
        self.assertEqual(wencai.counter()["cache_hit"], self._hit0 + 1)

    def test_query_normalized_same_cache(self):
        payload = {"datas": []}
        with mock.patch.object(wencai.subprocess, "run", self._fake_cli(payload)):
            wencai.iwencai("a  b   c", limit=1)
        with mock.patch.object(wencai.subprocess, "run", side_effect=RuntimeError):
            wencai.iwencai("a b c", limit=1)      # 多空格归一后同键 → 缓存命中

    def test_offline_missing_fixture_raises(self):
        wencai.set_offline(True)
        with self.assertRaises(wencai.FixtureMissing):
            wencai.iwencai("不存在的 query", limit=1)

    def test_failure_not_cached(self):
        with mock.patch.object(wencai.subprocess, "run", side_effect=RuntimeError("boom")):
            got = wencai.iwencai("失败查询", limit=1)
        self.assertEqual(got, [])
        self.assertEqual(os.listdir(self.dir), [])   # 失败不写缓存


if __name__ == "__main__":
    unittest.main()
