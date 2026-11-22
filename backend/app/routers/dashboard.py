from __future__ import annotations

from fastapi import APIRouter

from app.db import connect
from app.services.gate import gate_snapshot, today_str

router = APIRouter(tags=["总览"])


@router.get("/总览")
def dashboard():
    conn = connect()
    try:
        def cnt(status: str) -> int:
            return conn.execute(
                "SELECT COUNT(*) AS c FROM hang_tickets WHERE status=?", (status,)
            ).fetchone()["c"]

        day = today_str()
        # 日闸徽章与状态页、写闸同源，解冻后不会假绿
        gate = gate_snapshot(day, conn=conn)
        wm = conn.execute(
            "SELECT value FROM watermarks WHERE key='在途票'"
        ).fetchone()
        return {
            "今日": day,
            "日闸": gate["状态"],
            "日闸sealed": gate["sealed"],
            "在途水位": wm["value"] if wm else 0,
            "已收": cnt("已收"),
            "水洗": cnt("水洗"),
            "烘干": cnt("烘干"),
            "熨烫": cnt("熨烫"),
            "待取": cnt("待取"),
            "已取": cnt("已取"),
            "超期隔离": cnt("超期隔离"),
            "丢失隔离": cnt("丢失隔离"),
        }
    finally:
        conn.close()
