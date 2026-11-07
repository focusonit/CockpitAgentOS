import { useEffect, useState } from "react";
import type { ProtocolSpec } from "./protocol";

/** 拉取 /api/protocol；调试面板的控件都从这里生成。 */
export function useProtocol() {
  const [spec, setSpec] = useState<ProtocolSpec | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/protocol")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<ProtocolSpec>;
      })
      .then((body) => !cancelled && setSpec(body))
      .catch((err: unknown) => !cancelled && setError(`协议加载失败：${String(err)}`));
    return () => {
      cancelled = true;
    };
  }, []);

  return { spec, error };
}
