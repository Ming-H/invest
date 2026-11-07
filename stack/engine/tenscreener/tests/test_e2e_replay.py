"""若羽臣(003010)四时点端到端回放(offline fixtures)。

时间线(真实,2026-08-30 会话复盘验证):
  T1 2024-10-08  L1 初筛:24Q3 三线首次全过线(54.6/68.1/76.3),股价未动(近1年-18%)
                 → 若羽臣应在候选池,共振=triple;周期股应被剔除
  T2 2024-11-15  watch:三季报(10/30)披露后两周,价 9.2 破一年新高(pos 1.02),大盘 on
                 → scouted→confirmed
  T3 2025-04-30  年报+一季报连续过线 → confirm 信号含"连续验证"(资金合力)
  T4 2026-04-30  track:26Q1 增速降档(111.4→73.3,-38pp)+回撤49%,但三线仍全过线
                 → warn(中期调整裁定:扛住) 而非 exit —— 与真实后续(26Q3 反弹)一致
运行前先:python3 tests/gen_fixtures.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
CLI = os.path.join(SCRIPTS, "cli.py")


def run_cli(*argv, out_root=None, asof=None):
    import tempfile
    env = dict(os.environ)
    if out_root:
        env["TENSCREENER_OUT_ROOT"] = out_root
    if asof:
        env["TENSCREENER_ASOF"] = asof      # 冻结'今天': 免费路径K线端点/fixture 命名
    p = subprocess.run([sys.executable, CLI, *argv],
                       capture_output=True, text=True, timeout=120, env=env)
    return p.returncode, p.stdout, p.stderr


def cache(tp):
    return os.path.join(HERE, "cache", tp)


@unittest.skipUnless(os.path.exists(cache("2024-10-08")),
                     "fixtures 未生成:先运行 python3 tests/gen_fixtures.py")
class TestReplay(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.out = tempfile.mkdtemp()


    def test_t1_l1_scout(self):
        rc, out, err = run_cli("l1", "--offline", "--cache-dir", cache("2024-10-08"))
        self.assertEqual(rc, 0, err)
        cands = json.loads(out)
        names = [c["name"] for c in cands]
        self.assertIn("若羽臣", names)
        self.assertNotIn("测试周期股", names)                 # 周期股被剔
        ryc = next(c for c in cands if c["name"] == "若羽臣")
        q = ryc["quarterly"]
        self.assertEqual(q["resonance"], "triple")
        self.assertEqual(q["pass_lines"]["count"], 3)
        self.assertEqual(q["streak_max"], 3)                  # 归母/扣非 24Q1-Q3 连续过线
        self.assertIn(ryc["growth_cyclic"], ("neutral", "growth"))
        self.assertGreater(ryc["profit_growth"], 0)           # 字段断链修复

    def test_t2_watch_confirm(self):
        tmp = tempfile.mkdtemp()
        wlpath = os.path.join(tmp, "watchlist.json")
        rc, out, err = run_cli("watch", "add", "003010", "若羽臣",
                               "--watchlist", wlpath)
        self.assertEqual(rc, 0, err)
        rc, out, err = run_cli("watch", "--run", "--offline",
                               "--cache-dir", cache("2024-11-15"),
                               "--watchlist", wlpath, out_root=self.out,
                               asof="2024-11-15")
        self.assertEqual(rc, 0, err)
        self.assertIn("**confirmed**", out)
        self.assertIn("连续验证", out)                         # streak≥2 注记
        with open(wlpath, encoding="utf-8") as f:
            w = json.load(f)
        it = w["items"][0]
        self.assertEqual(it["state"], "confirmed")
        self.assertEqual(it["confirm"]["result"], "confirmed")
        self.assertEqual(len(it["confirm"]["signals"]), 3)
        self.assertEqual(it["last_period_seen"], "2024Q3")

    def test_t3_continuous_validation(self):
        tmp = tempfile.mkdtemp()
        wlpath = os.path.join(tmp, "watchlist.json")
        run_cli("watch", "add", "003010", "若羽臣", "--watchlist", wlpath)
        rc, out, err = run_cli("watch", "--run", "--offline",
                               "--cache-dir", cache("2025-04-30"),
                               "--watchlist", wlpath, out_root=self.out,
                               asof="2025-04-30")
        self.assertEqual(rc, 0, err)
        self.assertIn("**confirmed**", out)
        with open(wlpath, encoding="utf-8") as f:
            w = json.load(f)
        joined = "".join(w["items"][0]["confirm"]["signals"])
        self.assertIn("连续验证", joined)
        self.assertIn("2025Q1", joined)

    def test_t4_track_downshift_warn(self):
        tmp = tempfile.mkdtemp()
        wlpath = os.path.join(tmp, "watchlist.json")
        run_cli("watch", "add", "003010", "若羽臣", "--watchlist", wlpath)
        run_cli("watch", "--run", "--offline", "--cache-dir", cache("2024-11-15"),
                "--watchlist", wlpath, asof="2024-11-15")     # → confirmed
        # promote 需要 price_position 查询(用 T2 fixture 目录)
        rc, out, err = run_cli("track", "promote", "003010", "--cost", "25.6",
                               "--date", "2025-05-06", "--offline",
                               "--cache-dir", cache("2024-11-15"),
                               "--watchlist", wlpath, asof="2024-11-15")
        self.assertEqual(rc, 0, err)
        rc, out, err = run_cli("track", "--run", "--offline",
                               "--cache-dir", cache("2026-04-30"),
                               "--watchlist", wlpath, out_root=self.out,
                               asof="2026-04-30")
        self.assertEqual(rc, 0, err)
        self.assertIn("**warn**", out)
        self.assertIn("中期调整", out)
        with open(wlpath, encoding="utf-8") as f:
            w = json.load(f)
        it = w["items"][0]
        self.assertEqual(it["state"], "holding")              # warn 不迁移状态
        self.assertEqual(it["track"]["last_level"], "warn")
        self.assertGreaterEqual(it["track"]["peak_price"], 44.0)


if __name__ == "__main__":
    unittest.main()
