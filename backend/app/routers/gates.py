from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.db import connect
from app.services.gate import (
    gate_snapshot,
    list_events,
    seal_day,
    today_str,
    unseal_day,
)

router = APIRouter(tags=["日闸"])


class SealIn(BaseModel):
    day: str = ""
    note: str = ""
    operator: str = "店长"


@router.get("/日闸")
def list_gates():
    conn = connect()
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM day_gates ORDER BY day DESC"
            ).fetchall()
        ]
        # 状态文案统一由权威快照口径给出，路由不自行拼 已结案/开放
        for r in rows:
            r["状态"] = gate_snapshot(r["day"], conn=conn)["状态"]
        return rows
    finally:
        conn.close()


@router.get("/日闸/今日")
def today_gate():
    return gate_snapshot(today_str())


@router.post("/日闸/结案")
def do_seal(body: SealIn):
    return seal_day(body.day or None, body.note, body.operator)


@router.post("/日闸/解冻")
def do_unseal(body: SealIn):
    return unseal_day(body.day or None, body.note, body.operator)


@router.get("/日闸/流水")
def gate_events(day: str = ""):
    return list_events(day or None)
