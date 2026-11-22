"""HTTP 层冒烟：确认 409 序列化、新端点与读路径同源（独立临时库）。

运行：cd backend && ../../.venv/bin/python -m tests.test_http_gate
"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="hangslip-http-")
os.environ["HANGSLIP_DB"] = os.path.join(_tmp, "http.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

c = TestClient(app)
PASS = 0


def check(name, cond, extra=""):
    global PASS
    assert cond, f"[失败] {name} {extra}"
    PASS += 1
    print(f"  ok - {name}")


r = c.get("/api/日闸/今日")
check("初始开放", r.json()["状态"] == "开放" and r.json()["sealed"] == 0)

r = c.post("/api/日闸/结案", json={"note": "营业日结案"})
check("结案成功", r.status_code == 200 and r.json()["状态"] == "已结案")

r = c.post("/api/挂牌", json={
    "ticket_no": "挂-W1", "store_code": "店-河西", "pickup_code": "p",
    "customer_name": "网客", "items": [{"garment": "衬衣", "qty": 1}],
})
check("开票 HTTP 409", r.status_code == 409
      and r.json()["detail"] == "今日已结案，禁止新开挂牌")

r = c.post("/api/挂牌/挂-1002/推进", json={"to_status": "烘干"})
check("推进 HTTP 409", r.status_code == 409 and "禁止改工序" in r.json()["detail"])

r = c.post("/api/挂牌/挂-1001/取衣", json={"pickup_code": "暗码甲七"})
check("取衣 HTTP 409", r.status_code == 409 and "禁止取衣核销" in r.json()["detail"])

r = c.post("/api/挂牌/挂-1001/隔离", json={"kind": "超期隔离", "reason": "逾承诺日"})
check("隔离 HTTP 409", r.status_code == 409 and "禁止人工隔离" in r.json()["detail"])

r = c.get("/api/总览")
check("总览徽章同源已结案", r.json()["日闸"] == "已结案"
      and r.json()["日闸sealed"] == 1)

r = c.get("/api/日闸/流水")
actions = {e["action"] for e in r.json()}
check("流水可核(四类拦截+结案)",
      {"拦截-开票", "拦截-推进", "拦截-取衣", "拦截-隔离", "结案"} <= actions)

r = c.post("/api/日闸/解冻", json={"note": "次日解冻"})
check("解冻成功", r.status_code == 200 and r.json()["状态"] == "开放")
r = c.get("/api/总览")
check("解冻后总览不假绿", r.json()["日闸"] == "开放" and r.json()["日闸sealed"] == 0)

# 解冻后写恢复（取衣闭环）
r = c.post("/api/挂牌/挂-1001/取衣", json={"pickup_code": "暗码甲七"})
check("解冻后取衣恢复", r.status_code == 200 and r.json()["status"] == "已取")

print(f"\nHTTP 层 {PASS} 项核验通过。")
