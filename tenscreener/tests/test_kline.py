"""kline 腾讯K线封装 单测。"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import kline


def _rows(spec):
    """spec: [(date, close, high, low)] → kline 行。"""
    return [[d, str(c), str(c), str(h), str(l), "0"] for d, c, h, l in spec]


class TestMetrics(unittest.TestCase):
    def test_series_metrics_exact(self):
        rows = _rows([(f"2025-01-{i:02d}", 10 + i, 12 + i, 9 + i) for i in range(1, 26)])
        m = kline.series_metrics(rows, window=25)
        self.assertEqual(m["last"], 35.0)
        self.assertEqual(m["high_52w"], 37.0)
        self.assertEqual(m["low_52w"], 10.0)
        self.assertAlmostEqual(m["pos_1y"], 35 / 37)
        self.assertAlmostEqual(m["run_multiple"], 3.5)
        self.assertAlmostEqual(m["drawdown"], 1 - 35 / 37)

    def test_too_few_rows(self):
        self.assertEqual(kline.series_metrics(_rows([("2025-01-01", 1, 1, 1)])), {})

    def test_metrics_at_asof(self):
        rows = _rows([(f"{y}-{m:02d}-15", y * 12 + m, y * 12 + m, y * 12 + m)
                      for y in (2023, 2024, 2025) for m in range(1, 13)])
        m = kline.metrics_at(rows, "2025-06-30", window=6)
        self.assertEqual(m["last"], 2025 * 12 + 6)
        self.assertEqual(m["high_52w"], 2025 * 12 + 6)

    def test_index_gate_series(self):
        # 前 5 天 10,后 5 天 20 → ma5 从第5根起算;close≥ma → on
        rows = _rows([(f"2025-01-{i:02d}", 10 if i <= 5 else 20,
                       10 if i <= 5 else 20, 10 if i <= 5 else 20)
                      for i in range(1, 11)])
        gates = kline.index_gate_series(rows, window=5)
        self.assertEqual(len(gates), 6)                       # 10-5+1
        self.assertEqual([g[1] for g in gates], ["on"] * 6)   # 均升,恒 on
        down = _rows([(f"2025-01-{i:02d}", 30 - 2 * i, 30 - 2 * i, 30 - 2 * i)
                      for i in range(1, 11)])        # 严格递减 → close<ma
        gates2 = kline.index_gate_series(down, window=5)
        self.assertEqual(gates2[-1][1], "off")

    def test_gate_at(self):
        gates = [("2025-01-01", "on"), ("2025-02-01", "off")]
        self.assertEqual(kline.gate_at(gates, "2025-01-15"), "on")
        self.assertEqual(kline.gate_at(gates, "2025-03-01"), "off")
        self.assertEqual(kline.gate_at(gates, "2024-12-01", default="weak"), "weak")


class TestFetch(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        kline.set_cache_dir(self.dir)
        kline.set_offline(False)

    def tearDown(self):
        kline.set_cache_dir(None)
        kline.set_offline(False)

    def test_fetch_merges_chunks_and_caches(self):
        calls = []

        def fake_chunk(symbol, beg, end):
            calls.append((beg, end))
            if end <= "2024-01-01":
                return _rows([("2022-09-01", 1, 1, 1), ("2023-06-01", 2, 2, 2)])
            return _rows([("2023-06-01", 2, 2, 2), ("2024-06-01", 3, 3, 3),
                          ("2025-06-01", 4, 4, 4)])

        with mock.patch.object(kline, "_fetch_chunk", side_effect=fake_chunk):
            rows = kline.fetch_qfq("sz000001", "2022-07-01", "2025-06-01")
        self.assertEqual([r[0] for r in rows],
                         ["2022-09-01", "2023-06-01", "2024-06-01", "2025-06-01"])  # 去重升序
        self.assertEqual(len(calls), 2)                        # 两年一段,两段拉取
        self.assertEqual(kline.counter()["api"], 1)
        with mock.patch.object(kline, "_fetch_chunk", side_effect=RuntimeError):
            rows2 = kline.fetch_qfq("sz000001", "2022-07-01", "2025-06-01")
        self.assertEqual(rows2, rows)                          # 第二次走缓存

    def test_offline_missing_raises(self):
        kline.set_offline(True)
        with self.assertRaises(kline.FixtureMissing):
            kline.fetch_qfq("sz999999", "2024-01-01", "2024-12-31")


if __name__ == "__main__":
    unittest.main()
