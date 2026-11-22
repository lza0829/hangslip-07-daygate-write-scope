import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useDayGate } from "../useDayGate";

export default function Gate() {
  const { gate, sealed, 状态, reload } = useDayGate();
  const [rows, setRows] = useState([]);
  const [events, setEvents] = useState([]);
  const [msg, setMsg] = useState("");
  const [ok, setOk] = useState(false);

  const load = useCallback(async () => {
    const [list, ev] = await Promise.all([api.日闸列表(), api.日闸流水()]);
    setRows(list);
    setEvents(ev);
  }, []);

  useEffect(() => {
    load().catch((e) => setMsg(e.message));
  }, [load]);

  async function run(action) {
    setMsg("");
    setOk(false);
    try {
      const body = { note: action === "结案" ? "营业日结案" : "营业日解冻" };
      const row = action === "结案" ? await api.结案(body) : await api.解冻(body);
      setOk(true);
      setMsg(`已${action}：${row.day}（${row.状态}）`);
      await Promise.all([reload(), load()]);
    } catch (e) {
      setMsg(e.message);
    }
  }

  return (
    <section className="panel">
      <h3>结案日闸</h3>
      <p className={`msg ${ok ? "ok" : ""}`}>{msg}</p>

      {sealed && (
        <div className="gate-frozen">
          全日写冻结中：开票、工序推进、取衣核销、人工隔离均已关闭，仅保留只读总览与列表。
        </div>
      )}

      <div className="row">
        <div className="metric">
          <b>{gate?.day || "—"}</b>
          <span>今日</span>
        </div>
        <div className="metric">
          <b className={sealed ? "gate-sealed" : "gate-open"}>{状态}</b>
          <span>日闸状态</span>
        </div>
        <button type="button" onClick={() => run("结案")} disabled={sealed}>
          今日结案
        </button>
        <button type="button" className="ghost" onClick={() => run("解冻")} disabled={!sealed}>
          解冻
        </button>
      </div>

      <h4>日闸流水（结案 / 解冻 / 被拦写均可核）</h4>
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>动作</th>
            <th>目标票</th>
            <th>说明</th>
            <th>操作人</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{e.day}</td>
              <td>
                <span className={`badge ${e.action.startsWith("拦截") ? "丢失隔离" : "待取"}`}>
                  {e.action}
                </span>
              </td>
              <td>{e.target || "—"}</td>
              <td>{e.detail || "—"}</td>
              <td>{e.operator || "—"}</td>
              <td>{e.created_at}</td>
            </tr>
          ))}
          {events.length === 0 && (
            <tr>
              <td colSpan={6}>暂无流水</td>
            </tr>
          )}
        </tbody>
      </table>

      <h4>历史日闸</h4>
      <table>
        <thead>
          <tr>
            <th>日期</th>
            <th>状态</th>
            <th>结案时刻</th>
            <th>备注</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.day}>
              <td>{r.day}</td>
              <td>{r.状态}</td>
              <td>{r.sealed_at || "—"}</td>
              <td>{r.note || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
