"""Part B: proof for each audit finding the 0001 / 0002 patches fixed (AUDIT-report.pdf, commit 0b0411a).

REL-1   HANA timeouts             AuditFixed.test_rel1_timeouts_reach_the_driver_in_ms
                                  (units measured against the real driver: see the README)
REL-2   stale data kept on error  test_bridge.Reports.test_failed_refresh_serves_last_good_result
                                  test_contract.Section5LastGoodResult.test_stale_carries_the_earlier_read_time
REL-4   JSON 500, never HTML      AuditFixed.test_rel4_unexpected_error_is_json_500
PERF-1  refresh=1 rate limit      test_bridge.Reports.test_refresh_is_rate_limited
                                  AuditFixed.test_perf1_refresh_limit_per_key
SEC-4   no raw driver errors      test_bridge.Reports.test_hana_error_is_502_json
                                  test_contract.Section5LastGoodResult.test_nothing_cached_is_a_plain_502
BUG-2   TSV exponent / NaN        test_contract.Section4Tsv.test_every_value_rule
BUG-5   lenient integers          test_bridge.Lookups.test_ints_out_of_range, AuditFixed.test_bug5_whole_numbers_only
BUG-6   UTC offset                test_contract.Section4Headers.test_headers_when_fresh
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import fakedb
from test_bridge import ROOT, Base, bridge, hana

import config


class AuditFixed(Base):
    def test_rel1_timeouts_reach_the_driver_in_ms(self):
        hana.run = self._orig
        env = {k: v for k, v in os.environ.items() if k not in ("HANA_CONNECT_TIMEOUT_MS", "HANA_QUERY_TIMEOUT_MS")}
        with fakedb.installed(rows=[(1,)]) as db:
            with hana.configured(config.from_env(env)):
                hana.run("SELECT 1 FROM DUMMY")
            self.assertEqual(db.connect_kwargs["connectTimeout"], 15000)              # contract defaults, in ms
            self.assertEqual(db.connect_kwargs["communicationTimeout"], 240000)
            with hana.configured(config.from_env(dict(env, HANA_CONNECT_TIMEOUT_MS="5000",
                                                      HANA_QUERY_TIMEOUT_MS="90000"))):
                hana.run("SELECT 1 FROM DUMMY")
            self.assertEqual((db.connect_kwargs["connectTimeout"], db.connect_kwargs["communicationTimeout"]),
                             (5000, 90000))
            self.assertEqual(db.closed, 2)                                              # connection always closed

    def test_rel4_unexpected_error_is_json_500(self):
        orig = bridge._params
        bridge._params = lambda spec: (_ for _ in ()).throw(RuntimeError("internal detail C:\\sap-bridge"))
        try:
            r = self.c.get("/api/data/so_detail?docnum=1", headers=self.h)
        finally:
            bridge._params = orig
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.mimetype, "application/json")
        self.assertRegex(r.json["error"], r"^The bridge hit an unexpected error \(bridge log ref [0-9a-f]{8}\)\.$")
        self.assertNotIn("sap-bridge", r.json["error"])

    def test_perf1_refresh_limit_per_key(self):
        for _ in range(5):
            self.assertEqual(self.c.get("/api/reports/kpi?refresh=1", headers=self.h).status_code, 200)
            self.c.get("/api/reports/supply-demand?refresh=1", headers=self.h)
        self.assertEqual(len(self.calls), 2)                     # one HANA run per report, not ten

    def test_bug5_whole_numbers_only(self):
        for ok in ("7", "+7", " 7 "):
            self.assertEqual(self.c.get(f"/api/data/so_detail?docnum={ok}", headers=self.h).status_code, 200, ok)
        for bad in ("7.0", "1e3", "0x10", "７", "7 7", "--7"):
            r = self.c.get(f"/api/data/so_detail?docnum={bad}", headers=self.h)
            self.assertEqual(r.status_code, 400, bad)


class EnvFromOwnFolder(unittest.TestCase):
    def test_env_is_read_from_the_bridge_folder_not_the_working_directory(self):
        """Contract section 5: as a Windows service the process starts somewhere else."""
        with tempfile.TemporaryDirectory() as tmp:
            copy = pathlib.Path(tmp) / "bridge"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".env"))
            (copy / ".env").write_text("REFRESH_MIN_SECONDS=7\nAPI_KEYS=from-the-bridge-folder\n", encoding="utf-8")
            elsewhere = pathlib.Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            env = {k: v for k, v in os.environ.items() if k not in ("REFRESH_MIN_SECONDS", "API_KEYS")}
            out = subprocess.run(
                [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(copy)!r}); import app; "
                                       "print(app.SETTINGS.refresh_min_seconds, len(app.SETTINGS.api_keys))"],
                cwd=elsewhere, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.split()[-2:], ["7", "1"], out.stderr)


if __name__ == "__main__":
    unittest.main()
