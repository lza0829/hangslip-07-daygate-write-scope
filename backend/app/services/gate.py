"""日闸唯一权威源。

全日写冻结口径（全局只此一套）：
- 结案（sealed=1）后，当日一切业务写——新开票、工序推进、取衣核销、人工隔离——
  一律拒绝；只读总览与列表不受影响。
- 所有写接口必须在拿到写锁的事务内调用 assert_day_open()，
  不得在事务外用独立连接预判，否则结案与写并发时会产生半截挂牌。
- 被拦截的写、结案、解冻全部落 gate_events 流水，可核。
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import HTTPException

from app.db import connect

# 结案后各写动作的统一拒答文案，保证前端与流水口径一致
BLOCKED_MESSAGES = {
    "开票": "今日已结案，禁止新开挂牌",
    "推进": "今日已结案，禁止改工序",
    "取衣": "今日已结案，禁止取衣核销",
    "隔离": "今日已结案，禁止人工隔离",
}

GATE_OPEN = "开放"
GATE_SEALED = "已结案"


def today_str() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def gate_snapshot(day: str | None = None, conn=None) -> dict:
    """日闸状态的唯一读取口径。所有读路径（总览徽章、状态页、写接口）都走这里。"""
    d = day or today_str()
    own = conn is None
    if own:
        conn = connect()
    try:
        row = conn.execute(
            "SELECT day, sealed, sealed_at, note FROM day_gates WHERE day=?",
            (d,),
        ).fetchone()
        if not row:
            return {"day": d, "sealed": 0, "sealed_at": None, "note": "",
                    "状态": GATE_OPEN}
        data = dict(row)
        data["状态"] = GATE_SEALED if data["sealed"] else GATE_OPEN
        return data
    finally:
        if own:
            conn.close()


def is_day_sealed(day: str | None = None, conn=None) -> bool:
    return bool(gate_snapshot(day, conn=conn)["sealed"])


def ensure_day_row(conn, day: str | None = None) -> str:
    """在调用方事务内确保当日 day_gates 行存在（开放态）。"""
    d = day or today_str()
    row = conn.execute("SELECT day FROM day_gates WHERE day=?", (d,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO day_gates(day, sealed, sealed_at, note) VALUES (?,?,?,?)",
            (d, 0, None, ""),
        )
    return d


def _record_event(action: str, target: str, detail: str, operator: str,
                  day: str | None = None) -> None:
    """独立连接落日闸流水。仅在业务事务之外调用，避免自连接锁等待。"""
    d = day or today_str()
    conn = connect()
    try:
        conn.execute(
            """INSERT INTO gate_events(day, action, target, detail, operator, created_at)
               VALUES (?,?,?,?,?,?)""",
            (d, action, target or "", detail or "", operator or "", _now()),
        )
        conn.commit()
    finally:
        conn.close()


class DaySealedError(HTTPException):
    """结案写闸拦截：携带动作与目标，供外层回滚后补记可核流水。"""

    def __init__(self, action: str, target: str = "", operator: str = "",
                 day: str | None = None):
        self.action = action
        self.target = target
        self.operator = operator
        self.day = day or today_str()
        super().__init__(409, BLOCKED_MESSAGES.get(action, "今日已结案，禁止写入"))


def assert_day_open(conn, action: str, target: str = "",
                    operator: str = "", day: str | None = None) -> str:
    """事务内写闸断言。必须在 BEGIN IMMEDIATE 拿到写锁之后调用。

    命中冻结直接抛 DaySealedError，由 gated_write 捕获回滚并记流水，
    因此不会留下任何半截挂牌/事件/水位写入。
    """
    d = day or today_str()
    if is_day_sealed(d, conn=conn):
        raise DaySealedError(action, target, operator, d)
    return d


def refresh_in_flight(conn) -> int:
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM hang_tickets WHERE status NOT IN ('已取','丢失隔离')"
    ).fetchone()["c"]
    conn.execute(
        """INSERT INTO watermarks(key, value, updated_at) VALUES ('在途票', ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (n, _now()),
    )
    return n


def seal_day(day: str | None = None, note: str = "", operator: str = "店长") -> dict:
    """结案。BEGIN IMMEDIATE 与业务写互斥，保证结案与开票争用时只有一方生效。"""
    d = day or today_str()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_day_row(conn, d)
        conn.execute(
            "UPDATE day_gates SET sealed=1, sealed_at=?, note=? WHERE day=?",
            (_now(), note, d),
        )
        conn.execute(
            """INSERT INTO gate_events(day, action, target, detail, operator, created_at)
               VALUES (?,?,?,?,?,?)""",
            (d, "结案", "", note or "营业日结案", operator, _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return gate_snapshot(d)


def unseal_day(day: str | None = None, note: str = "", operator: str = "店长") -> dict:
    """解冻（解冻入口由本实现提供）。只清冻结态，结案历史保留在 gate_events。"""
    d = day or today_str()
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ensure_day_row(conn, d)
        conn.execute(
            "UPDATE day_gates SET sealed=0, sealed_at=NULL, note=? WHERE day=?",
            (note, d),
        )
        conn.execute(
            """INSERT INTO gate_events(day, action, target, detail, operator, created_at)
               VALUES (?,?,?,?,?,?)""",
            (d, "解冻", "", note or "营业日解冻", operator, _now()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return gate_snapshot(d)


def record_blocked(err: "DaySealedError") -> None:
    """业务事务回滚后调用：把被日闸拦截的写落流水（失败可核）。"""
    _record_event(
        action=f"拦截-{err.action}",
        target=err.target,
        detail=BLOCKED_MESSAGES.get(err.action, "今日已结案，禁止写入"),
        operator=err.operator,
        day=err.day,
    )


def list_events(day: str | None = None) -> list[dict]:
    conn = connect()
    try:
        if day:
            rows = conn.execute(
                "SELECT * FROM gate_events WHERE day=? ORDER BY id DESC", (day,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM gate_events ORDER BY id DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
