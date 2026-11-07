import { useEffect, useState } from "react";
import type { AppsData } from "./protocol";

/** 拉取 /api/apps：商户和菜单、曲库、地图点位。 */
export function useAppsData() {
  const [data, setData] = useState<AppsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/apps")
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<AppsData>;
      })
      .then((body) => !cancelled && setData(body))
      .catch((err: unknown) => !cancelled && setError(`App 数据加载失败：${String(err)}`));
    return () => {
      cancelled = true;
    };
  }, []);

  return { data, error };
}
