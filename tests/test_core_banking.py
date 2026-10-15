"""
Pruebas de invariantes del motor regulatorio, del ledger y del core bitemporal.
Ejecutar:  .venv/bin/python -m unittest discover -s tests -v
Cada prueba de base de datos usa un archivo SQLite temporal propio.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db_layer as db  # noqa: E402
import regulatory_engine as reg  # noqa: E402


class RegulatoryEngineTests(unittest.TestCase):
    def test_no_caps_binding(self):
        r = reg.compute_lcr(1000, 200, 50, 800, 300)
        self.assertEqual(r.adjustment_15pct_cap, Decimal("0.0000"))
        self.assertEqual(r.adjustment_40pct_cap, Decimal("0.0000"))
        self.assertEqual(r.hqla, Decimal("1250.0000"))
        self.assertEqual(r.net_cash_outflows, Decimal("500.0000"))
        self.assertEqual(r.lcr_pct, Decimal("250.0000"))

    def test_level2b_cap_15pct(self):
        # L2B = 100 sobre L1 = 400: el tope deja L2B en 15% del stock final.
        r = reg.compute_lcr(400, 0, 100, 100, 0)
        self.assertEqual(r.hqla, Decimal("470.5882"))
        self.assertAlmostEqual(float((r.level2b_post_haircut - r.adjustment_15pct_cap) / r.hqla), 0.15, places=6)

    def test_level2_cap_40pct(self):
        r = reg.compute_lcr(300, 500, 0, 100, 0)
        self.assertEqual(r.hqla, Decimal("500.0000"))  # L2 limitado a 2/3 de L1
        self.assertAlmostEqual(float((r.hqla - r.level1) / r.hqla), 0.40, places=6)

    def test_inflow_cap_75pct(self):
        r = reg.compute_lcr(100, 0, 0, 1000, 900)
        self.assertEqual(r.capped_inflows, Decimal("750.0000"))
        self.assertEqual(r.inflow_cap_adjustment, Decimal("150.0000"))
        self.assertEqual(r.net_cash_outflows, Decimal("250.0000"))

    def test_money_never_uses_binary_float(self):
        self.assertEqual(reg.to_money(0.1) + reg.to_money(0.2), Decimal("0.3000"))


class LedgerAndBitemporalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig = db.DB_FILE
        db.DB_FILE = os.path.join(self.tmp, "t.db")
        conn = db.get_connection()
        conn.executescript(db.DDL)
        db._seed_reference_data(conn)
        self.conn = conn
        self.now = datetime(2025, 3, 3, 14, 0)
        db.post_journal_entry("OPEN", "TEST", "APERTURA", "Capital inicial", [
            {"line_number": 1, "account_id": 110101, "contract_id": db.CASH_CONTRACT, "partner_id": 1005, "amount_debit": "1000"},
            {"line_number": 2, "account_id": 310101, "contract_id": 31001, "partner_id": 9001, "amount_credit": "1000"},
        ], now=self.now, conn=conn)

    def tearDown(self):
        self.conn.close()
        db.DB_FILE = self._orig

    def _transfer(self, key, amount, **kw):
        return db.post_journal_entry(key, "TEST", "COMPRA", "Compra", [
            {"line_number": 1, "account_id": 130101, "contract_id": 70100, "partner_id": 1006, "amount_debit": str(amount)},
            {"line_number": 2, "account_id": 110101, "contract_id": db.CASH_CONTRACT, "partner_id": 1005, "amount_credit": str(amount)},
        ], now=kw.pop("now", self.now), conn=self.conn, **kw)

    def test_unbalanced_entry_rejected(self):
        with self.assertRaises(db.LedgerRejection) as ctx:
            db.post_journal_entry("BAD", "TEST", "X", "x", [
                {"line_number": 1, "account_id": 130101, "contract_id": 70100, "partner_id": 1, "amount_debit": "10"},
                {"line_number": 2, "account_id": 110101, "contract_id": db.CASH_CONTRACT, "partner_id": 1, "amount_credit": "9"},
            ], now=self.now, conn=self.conn)
        self.assertEqual(ctx.exception.code, "DESCUADRE_CONTABLE")

    def test_idempotent_replay_and_conflict(self):
        first = self._transfer("K1", 100)
        again = self._transfer("K1", 100, now=self.now + timedelta(seconds=5))
        self.assertFalse(first["replayed"])
        self.assertTrue(again["replayed"])
        self.assertEqual(first["journal_entry_id"], again["journal_entry_id"])
        with self.assertRaises(db.IdempotencyConflict):
            self._transfer("K1", 999)
        n = self.conn.execute("SELECT COUNT(*) FROM oltp_journal_entry;").fetchone()[0]
        self.assertEqual(n, 2)

    def test_overdraft_blocked_and_failure_replayed(self):
        with self.assertRaises(db.LedgerRejection) as ctx:
            self._transfer("BIG", 5000)
        self.assertEqual(ctx.exception.code, "SOBREGIRO_NO_PERMITIDO")
        rec = self.conn.execute("SELECT status FROM oltp_idempotency_records WHERE idempotency_key='BIG';").fetchone()
        self.assertEqual(rec[0], "FAILED")
        replay = self._transfer("BIG", 5000)
        self.assertEqual(replay["status"], "FAILED")

    def test_immutability_triggers(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE oltp_ledger_posting SET amount_debit_e4 = 1;")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM oltp_journal_entry;")

    def test_two_phase_partial_post_and_void(self):
        p = self._transfer("P1", 300, phase="PENDING", pending_timeout=timedelta(days=1))
        bal = self.conn.execute("SELECT credits_pending_e4 FROM oltp_account_balance WHERE account_id=110101;").fetchone()[0]
        self.assertEqual(bal, db.m2i(300))
        db.resolve_pending_entry("P1-POST", p["journal_entry_id"], "POST",
                                 post_amounts={1: Decimal(200), 2: Decimal(200)}, now=self.now, conn=self.conn)
        row = self.conn.execute("SELECT credits_pending_e4, credits_posted_e4 FROM oltp_account_balance "
                                "WHERE account_id=110101 AND contract_id=?;", (db.CASH_CONTRACT,)).fetchone()
        self.assertEqual(row[0], 0)
        self.assertEqual(row[1], db.m2i(200))
        with self.assertRaises(db.LedgerRejection):
            db.resolve_pending_entry("P1-VOID", p["journal_entry_id"], "VOID", now=self.now, conn=self.conn)

    def test_pending_expiry(self):
        p = self._transfer("P2", 100, phase="PENDING", pending_timeout=timedelta(hours=1))
        out = db.expire_pending_entries(now=self.now + timedelta(hours=2), conn=self.conn)
        self.assertEqual(len(out), 1)
        kind = self.conn.execute("SELECT entry_kind FROM oltp_journal_entry WHERE pending_entry_id=?;",
                                 (p["journal_entry_id"],)).fetchone()[0]
        self.assertEqual(kind, "EXPIRE_PENDING")

    def test_hash_chain_detects_tampering(self):
        self._transfer("K2", 50)
        ok, _ = db.verify_ledger_cryptographic_chain(conn=self.conn)
        self.assertTrue(ok)
        # Simula un DBA privilegiado que desactiva el trigger y altera un monto.
        self.conn.execute("DROP TRIGGER trg_posting_no_update;")
        self.conn.execute("UPDATE oltp_ledger_posting SET amount_debit_e4 = amount_debit_e4 + 1 WHERE line_number = 1 "
                          "AND journal_entry_id = (SELECT MAX(journal_entry_id) FROM oltp_journal_entry);")
        ok, df = db.verify_ledger_cryptographic_chain(conn=self.conn)
        self.assertFalse(ok)
        self.assertIn("DETALLE_ALTERADO", set(df["status"]))

    def _contract(self):
        return db._golden_contract(90210, 1002, "OPER-COM-90210", "COM_CREDITO", "CL-SCL-01", 140101, date(2024, 12, 15),
                                   date(2026, 12, 15), Decimal(45_000_000), "0.085", "FIJO", 1, "BULLET", "C", "A2",
                                   "INFLOW_COMERCIAL")

    def test_bitemporal_correction_preserves_history(self):
        t0 = datetime(2024, 12, 15, 13, 0)
        db.bitemporal_insert(90210, self._contract(), date(2024, 12, 15), now=t0, conn=self.conn)
        jid = self.conn.execute("SELECT MAX(journal_entry_id) FROM oltp_journal_entry;").fetchone()[0]
        db.bitemporal_correction(90210, date(2024, 12, 28), {"current_balance_e4": db.m2i(42_000_000)},
                                 reason="abono retroactivo", source_journal_entry_id=jid,
                                 now=datetime(2025, 2, 5, 14, 0), conn=self.conn)
        as_reported = db.query_bitemporal_time_travel("2024-12-31 12:00:00", "2025-01-15 12:00:00", conn=self.conn)
        as_known = db.query_bitemporal_time_travel("2024-12-31 12:00:00", "2025-02-10 12:00:00", conn=self.conn)
        self.assertEqual(as_reported["current_balance"].iloc[0], 45_000_000)
        self.assertEqual(as_known["current_balance"].iloc[0], 42_000_000)

    def test_bitemporal_overlap_and_delete_blocked(self):
        t0 = datetime(2024, 12, 15, 13, 0)
        db.bitemporal_insert(90210, self._contract(), date(2024, 12, 15), now=t0, conn=self.conn)
        with self.assertRaises(sqlite3.IntegrityError):
            db._insert_core_row(self.conn, 90210, self._contract(), "2025-01-01 00:00:00.00", db.END_OF_TIME,
                                db.ts(t0 + timedelta(days=1)), "INSERT", None, None, t0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM core_contract_profile;")

    def test_update_cannot_be_retroactive_and_future_assertion_is_invisible(self):
        t0 = datetime(2024, 12, 15, 13, 0)
        db.bitemporal_insert(90210, self._contract(), date(2024, 12, 15), now=t0, conn=self.conn)
        with self.assertRaises(db.BitemporalViolation):
            db.bitemporal_update(90210, date(2024, 12, 1), {"contract_status": "MOROSO"}, reason="x",
                                 now=datetime(2025, 1, 10, 13), conn=self.conn)
        cutover = datetime(2025, 6, 1, 4, 0)
        db.bitemporal_future_assertion(90210, date(2025, 6, 1), {"cmf_rating": "B1"}, cutover, reason="migración",
                                       now=datetime(2025, 5, 20, 13), conn=self.conn)
        before = db.query_bitemporal_time_travel("2025-06-02 12:00:00", "2025-05-31 12:00:00", conn=self.conn)
        after = db.query_bitemporal_time_travel("2025-06-02 12:00:00", "2025-06-01 05:00:00", conn=self.conn)
        rating = lambda df: self.conn.execute(
            "SELECT cmf_rating FROM core_contract_profile WHERE contract_id=90210 AND asserted_from=? AND valid_from<=?",
            (df["asserted_from"].iloc[0], "2025-06-02 12:00:00.00")).fetchall()[-1][0]
        self.assertEqual(rating(before), "A2")
        self.assertEqual(rating(after), "B1")


if __name__ == "__main__":
    unittest.main()
