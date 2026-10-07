"""LISTEN 叫醒为主，另有短间隔兜底认领，避免通知丢失。"""
import threading
import time
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from db import DSN, SCHEMA, connect
from rules import judge, oil_block_reason


def claim_id(conn, scan_id: int | None) -> bool:
    with conn.transaction():
        if scan_id is not None:
            row = conn.execute(
                """SELECT s.id, s.fill_factor, t.oil_expiry_date AS expiry
                   FROM iv_scans s
                   LEFT JOIN transformers t ON t.id = s.transformer_id
                   WHERE s.id = %s AND s.status = 'pending'
                   FOR UPDATE OF s SKIP LOCKED""",
                (scan_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT s.id, s.fill_factor, t.oil_expiry_date AS expiry
                   FROM iv_scans s
                   LEFT JOIN transformers t ON t.id = s.transformer_id
                   WHERE s.status = 'pending' ORDER BY s.id
                   FOR UPDATE OF s SKIP LOCKED LIMIT 1"""
            ).fetchone()
        if row is None:
            return False
        now = datetime.now(timezone.utc)
        # 兜底：报送后箱变油样才过期的，工人同样不得让它过台，
        # 与报送拦截口读同一到期日，结论只有一种。
        block = oil_block_reason(row["expiry"], now.date()) if row["expiry"] else None
        if block:
            conn.execute(
                """UPDATE iv_scans SET status='blocked', verdict=NULL, reason=%s,
                   processed_at=%s WHERE id=%s""",
                (block, now, row["id"]),
            )
        else:
            verdict, reason = judge(float(row["fill_factor"]))
            conn.execute(
                """UPDATE iv_scans SET status='done', verdict=%s, reason=%s, processed_at=%s
                   WHERE id=%s""",
                (verdict, reason, now, row["id"]),
            )
    return True


def drain(conn) -> bool:
    any_row = False
    while claim_id(conn, None):
        any_row = True
    return any_row


def poll_loop():
    while True:
        try:
            with connect() as conn:
                drain(conn)
                conn.commit()
        except Exception as exc:
            print(f"poll error: {exc}", flush=True)
        time.sleep(0.6)


def listen_loop():
    while True:
        try:
            with psycopg.connect(DSN, row_factory=dict_row, autocommit=True) as conn:
                conn.execute("LISTEN iv_scan_new")
                for note in conn.notifies():
                    with connect() as work:
                        if note is not None:
                            claim_id(work, int(note.payload))
                        drain(work)
                        work.commit()
        except Exception as exc:
            print(f"listen error: {exc}", flush=True)
            time.sleep(1.5)


def main():
    with connect() as conn:
        conn.execute(SCHEMA)
        conn.commit()
    print("pv iv-scan notify worker started", flush=True)
    threading.Thread(target=listen_loop, name="iv-listen", daemon=True).start()
    poll_loop()


if __name__ == "__main__":
    main()
