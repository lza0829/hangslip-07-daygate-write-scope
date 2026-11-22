"""全日写冻结口径核验脚本（独立临时库，不碰开发数据）。

运行：cd backend && ../../.venv/bin/python -I -m tests.test_day_gate
"""
from __future__ import annotations

import os
import tempfile
import threading

_tmp = tempfile.mkdtemp(prefix="hangslip-test-")
os.environ["HANGSLIP_DB"] = os.path.join(_tmp, "test.db")

from fastapi import HTTPException  # noqa: E402

from app.db import connect, init_db  # noqa: E402
from app.seed import seed_if_empty  # noqa: E402
from app.services import tickets as t  # noqa: E402
from app.services.gate import (  # noqa: E402
    DaySealedError,
    gate_snapshot,
    list_events,
    seal_day,
    unseal_day,
)
from app.routers import dashboard as dash_router  # noqa: E402
from app.routers import gates as gate_router  # noqa: E402

seed_if_empty()
PASS = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS
    assert cond, f"[失败] {name} {extra}"
    PASS += 1
    print(f"  ok - {name}")


def block(attr, *args, **kwargs):
    """调用写接口，期望抛 409 DaySealedError。"""
    try:
        getattr(t, attr)(*args, **kwargs)
    except DaySealedError as e:
        assert e.status_code == 409
        return e
    raise AssertionError(f"{attr} 未被日闸拦截")


def raw(sql, args=()):
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


print("== 1. 开放日：在途票可正常收口 ==")
t.create_ticket({
    "ticket_no": "挂-T01", "store_code": "店-河西", "pickup_code": "暗码测试",
    "customer_name": "测试客", "items": [{"garment": "衬衣", "qty": 1}],
})
t.advance("挂-T01", "待取", "工序员")
t.pickup("挂-T01", "暗码测试", "柜员")
check("开放日取衣核销成功", raw(
    "SELECT status FROM hang_tickets WHERE ticket_no='挂-T01'")[0]["status"] == "已取")

print("== 2. 结案后四类写全部 409，且无半截挂牌 ==")
seal_day(note="营业日结案")
snap = gate_snapshot()
check("权威快照=已结案", snap["状态"] == "已结案" and snap["sealed"] == 1)

# 2.1 开票被拦
before = raw("SELECT COUNT(*) AS c FROM hang_tickets")[0]["c"]
e = block("create_ticket", {
    "ticket_no": "挂-BLOCK", "store_code": "店-河西", "pickup_code": "x",
    "customer_name": "拦", "items": [{"garment": "大衣", "qty": 2}],
})
after = raw("SELECT COUNT(*) AS c FROM hang_tickets")[0]["c"]
check("开票被拦后挂牌数不变", before == after)
check("无半截挂牌主表", not raw("SELECT 1 FROM hang_tickets WHERE ticket_no='挂-BLOCK'"))
check("无半截衣物行", not raw("SELECT 1 FROM ticket_items WHERE ticket_no='挂-BLOCK'"))
check("无半截工序事件", not raw("SELECT 1 FROM process_events WHERE ticket_no='挂-BLOCK'"))

# 2.2 推进被拦（种子 挂-1002 当前水洗）
check("推进前状态=水洗", raw(
    "SELECT status FROM hang_tickets WHERE ticket_no='挂-1002'")[0]["status"] == "水洗")
block("advance", "挂-1002", "烘干", "工序员")
check("推进被拦状态不变", raw(
    "SELECT status FROM hang_tickets WHERE ticket_no='挂-1002'")[0]["status"] == "水洗")

# 2.3 取衣被拦（种子 挂-1001 待取，暗码甲七）
block("pickup", "挂-1001", "暗码甲七", "柜员")
check("取衣被拦仍待取", raw(
    "SELECT status FROM hang_tickets WHERE ticket_no='挂-1001'")[0]["status"] == "待取")
check("无取衣核销记录", not raw("SELECT 1 FROM pickups WHERE ticket_no='挂-1001'"))

# 2.4 人工隔离被拦（原先漏检的口径）
block("quarantine", "挂-1001", "超期隔离", "逾承诺日", "店长")
check("隔离被拦仍待取", raw(
    "SELECT status FROM hang_tickets WHERE ticket_no='挂-1001'")[0]["status"] == "待取")
check("无隔离记录", not raw("SELECT 1 FROM quarantines WHERE ticket_no='挂-1001'"))

print("== 3. 拦截可核：四类拦截均落 gate_events ==")
actions = {r["action"] for r in list_events(snap["day"])}
for a in ("拦截-开票", "拦截-推进", "拦截-取衣", "拦截-隔离", "结案"):
    check(f"流水含 {a}", a in actions, str(actions))

print("== 4. 读路径同源：总览徽章/状态页/列表 口径一致 ==")
d = dash_router.dashboard()
check("总览徽章=已结案", d["日闸"] == "已结案" and d["日闸sealed"] == 1)
check("状态页今日=已结案", gate_router.today_gate()["状态"] == "已结案")
rows = gate_router.list_gates()
today_row = next(r for r in rows if r["day"] == snap["day"])
check("日闸列表状态同源", today_row["状态"] == "已结案")

print("== 5. 解冻后不假绿，写恢复 ==")
unseal_day(note="次日解冻")
check("权威快照回到开放", gate_snapshot()["状态"] == "开放"
      and gate_snapshot()["sealed"] == 0)
d2 = dash_router.dashboard()
check("总览徽章同步开放(不假绿)", d2["日闸"] == "开放" and d2["日闸sealed"] == 0)
check("状态页同步开放", gate_router.today_gate()["状态"] == "开放")
t.advance("挂-1002", "烘干", "工序员")
check("解冻后推进恢复", raw(
    "SELECT status FROM hang_tickets WHERE ticket_no='挂-1002'")[0]["status"] == "烘干")
check("解冻留痕", "解冻" in {r["action"] for r in list_events(snap["day"])})

print("== 6. 并发：结案 vs 开票，争用可核、无半截票 ==")
# 重置一个干净的开放日
seal_day(note="先结案")
unseal_day(note="再放开")
n_threads = 12
barrier = threading.Barrier(n_threads)
results: list[str] = []
lock = threading.Lock()


def creator(i: int) -> None:
    barrier.wait()
    no = f"挂-C{i:03d}"
    try:
        t.create_ticket({
            "ticket_no": no, "store_code": "店-城东", "pickup_code": f"p{i}",
            "customer_name": f"并发{i}",
            "items": [{"garment": "西装", "qty": 1}, {"garment": "领带", "qty": 2}],
        })
        with lock:
            results.append(f"ok:{no}")
    except DaySealedError:
        with lock:
            results.append(f"blocked:{no}")
    except HTTPException as e:
        with lock:
            results.append(f"other{e.status_code}:{no}")


def sealer() -> None:
    barrier.wait()
    seal_day(note="并发结案")


threads = [threading.Thread(target=creator, args=(i,)) for i in range(n_threads - 1)]
threads.append(threading.Thread(target=sealer))
for th in threads:
    th.start()
for th in threads:
    th.join()

ok_nos = [r.split(":", 1)[1] for r in results if r.startswith("ok:")]
blocked_nos = [r.split(":", 1)[1] for r in results if r.startswith("blocked:")]
others = [r for r in results if r.startswith("other")]
check("无线程遇到非闸错误", not others, str(others))
check("每个开票线程都有确定结局", len(results) == n_threads - 1)

# 所有成功票必须完整：主表 + 2 件衣物 + 1 条开工序事件，无半截
for no in ok_nos:
    check(f"{no} 主表存在", bool(raw("SELECT 1 FROM hang_tickets WHERE ticket_no=?", (no,))))
    check(f"{no} 衣物完整(2行)", len(raw(
        "SELECT * FROM ticket_items WHERE ticket_no=?", (no,))) == 2)
    check(f"{no} 工序事件完整", len(raw(
        "SELECT * FROM process_events WHERE ticket_no=?", (no,))) == 1)
# 被拦票必须彻底不存在
for no in blocked_nos:
    check(f"{no} 被拦后无任何残留", not raw(
        "SELECT 1 FROM hang_tickets WHERE ticket_no=?", (no,)))

ev = list_events(gate_snapshot()["day"])
blocked_events = {r["target"] for r in ev if r["action"] == "拦截-开票"}
check("每个被拦开票都有可核流水", set(blocked_nos).issubset(blocked_events),
      f"{blocked_nos} vs {blocked_events}")
check("成功与被拦之和=总尝试", len(ok_nos) + len(blocked_nos) == n_threads - 1)

# 结案一旦落库，后续再开票必被拦（口径不回退）
e2 = block("create_ticket", {
    "ticket_no": "挂-AFTER", "store_code": "店-城东", "pickup_code": "z",
    "customer_name": "结案后", "items": [{"garment": "马甲", "qty": 1}],
})
check("结案后新开票稳定被拦", e2.action == "开票")

print(f"\n全部 {PASS} 项核验通过。")
