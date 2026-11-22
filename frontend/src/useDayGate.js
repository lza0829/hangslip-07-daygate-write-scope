import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

/**
 * 日闸唯一前端口径：所有页面都通过本 hook 读取同一快照（/api/日闸/今日）。
 * sealed=true 即全日写冻结，各写入口据此禁用；只读总览与列表不受影响。
 */
export function useDayGate() {
  const [gate, setGate] = useState(null);
  const [err, setErr] = useState("");

  const reload = useCallback(() => {
    return api
      .日闸今日()
      .then((g) => {
        setGate(g);
        setErr("");
        return g;
      })
      .catch((e) => {
        setErr(e.message);
        throw e;
      });
  }, []);

  useEffect(() => {
    reload().catch(() => {});
  }, [reload]);

  return {
    gate,
    sealed: !!gate && gate.sealed === 1,
    状态: gate?.状态 || "—",
    day: gate?.day || "",
    err,
    reload,
  };
}
