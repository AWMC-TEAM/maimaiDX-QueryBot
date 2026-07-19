"""发票 returnCode=0 / null 成功失败率统计。"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from libraries.maimaidx_account_db import AccountDatabase


class TicketReturnCodeStatsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = AccountDatabase(Path(self.tmp.name) / "account.db")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_flags_detect_zero_and_null(self) -> None:
        self.assertEqual(
            AccountDatabase._ticket_detail_flags("上游 returnCode=0，票券未发放"),
            (True, False),
        )
        self.assertEqual(
            AccountDatabase._ticket_detail_flags(
                '充值失败, result={"returnCode": 0, "apiName": "UpsertUserChargelogApi"}'
            ),
            (True, False),
        )
        self.assertEqual(
            AccountDatabase._ticket_detail_flags("发票队列任务已完成（未返回 returnCode）"),
            (False, True),
        )
        self.assertEqual(
            AccountDatabase._ticket_detail_flags("returnCode=null"),
            (False, True),
        )
        self.assertEqual(
            AccountDatabase._ticket_detail_flags("returnCode=1"),
            (False, False),
        )
        # 不应把 returnCode=10 误判为 0
        self.assertEqual(
            AccountDatabase._ticket_detail_flags("returnCode=10"),
            (False, False),
        )

    def test_aggregate_rates(self) -> None:
        now = time.time()
        rows = [
            ("R1", "u1", "ticket", "success", "multiple=2", now),
            ("R2", "u1", "ticket", "success", "multiple=3", now),
            ("R3", "u1", "ticket", "error", "上游 returnCode=0，票券未发放", now),
            ("R4", "u2", "ticket", "error", "未返回 returnCode", now),
            ("R5", "u1", "upload", "success", "ok", now),
        ]
        with self.db._lock:
            self.db._conn.executemany(
                """INSERT INTO account_operation_log
                   (ref_id, user_key, operation, status, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self.db._conn.commit()

        global_stats = self.db.get_ticket_stats()
        self.assertEqual(global_stats["total"], 4)
        self.assertEqual(global_stats["success"], 2)
        self.assertEqual(global_stats["error"], 2)
        self.assertEqual(global_stats["success_rate"], 50.0)
        self.assertEqual(global_stats["error_rate"], 50.0)
        self.assertEqual(global_stats["return_code_0"], 1)
        self.assertEqual(global_stats["return_code_null"], 1)
        self.assertEqual(global_stats["return_code_0_rate"], 25.0)

        user_stats = self.db.get_ticket_stats(user_key="u1")
        self.assertEqual(user_stats["total"], 3)
        self.assertEqual(user_stats["return_code_0"], 1)
        self.assertEqual(user_stats["return_code_null"], 0)

        text = AccountDatabase.format_ticket_stats(global_stats)
        self.assertIn("成功：2（50.0%）", text)
        self.assertIn("失败：2（50.0%）", text)
        self.assertIn("returnCode=0：1", text)

    def test_failure_buckets_count_all_account_ops(self) -> None:
        now = datetime(2026, 7, 19, 17, 10, 0)
        base = now.timestamp()
        rows = [
            ("B1", "u1", "ticket", "success", "ok", base - 600),
            ("B2", "u1", "ticket", "error", "上游 returnCode=0，票券未发放", base - 500),
            ("B3", "u1", "upload_lx", "error", "maiu 上传失败", base - 400),
            ("B4", "u1", "bind", "success", "绑定成功", base - 300),
            ("B5", "u1", "bind", "error", "绑定失败", base - 200),
            # 另一半小时桶
            ("B6", "u1", "status", "success", "ok", base - 2000),
            # 窗口外，不应计入
            ("B7", "u1", "ticket", "error", "old", base - 50 * 3600),
        ]
        with self.db._lock:
            self.db._conn.executemany(
                """INSERT INTO account_operation_log
                   (ref_id, user_key, operation, status, detail, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )
            self.db._conn.commit()

        series = self.db.get_account_failure_buckets(hours=48, minutes=30, now=now)
        # 无数据时段省略，只剩有操作的桶
        self.assertEqual(len(series), 2)
        # 较新的桶：5 条（ticket×2 + upload + bind×2）
        newest = series[-1]
        self.assertEqual(newest[3], 5)
        self.assertEqual(newest[2], 3)
        self.assertAlmostEqual(newest[1], 60.0, places=3)
        self.assertTrue(
            self.db.account_op_is_failure(
                "error",
                "发票队列任务执行失败（上游 returnCode=0，票券未发放）；本次不扣 BREAK",
            )
        )


if __name__ == "__main__":
    unittest.main()
