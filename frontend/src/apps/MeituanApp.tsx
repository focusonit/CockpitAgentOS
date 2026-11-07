import { useState } from "react";
import type { Merchant } from "../lib/protocol";
import { Btn, hotRing, read, touched, type AppProps } from "./types";

interface CartItem {
  item: string;
  count: number;
  size: string;
  note: string;
  price: number;
}

interface Draft {
  merchant: string;
  items: CartItem[];
  address: string;
  total: number;
  kind: string;
}

interface Order extends Draft {
  id: string;
  status: string;
  placed_at: string;
}

interface Booking {
  merchant: string;
  people: number;
  time: string;
  room: boolean;
}

export interface MeituanProps extends AppProps {
  /** 大脑挂起了一个需要确认的操作（例如下单），确认按钮应当回答它，而不是自己下单。 */
  confirmPending: boolean;
  onEvent: (name: string) => void;
}

const ADDRESSES = ["公司", "家", "当前位置"];

function itemsText(items: CartItem[]): string {
  return items.map((i) => `${i.item}${i.size ? `（${i.size}）` : ""}×${i.count}`).join("、");
}

function DraftCard({ draft, confirmPending, onConfirm, onCancel, onAddress }: {
  draft: Draft;
  confirmPending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onAddress: (a: string) => void;
}) {
  return (
    <div className="rounded border border-safe bg-safe-bg p-2 text-sm" role="group" aria-label="订单草稿">
      <p className="font-medium text-safe">待确认订单{confirmPending ? "（等待你确认）" : ""}</p>
      <p>{draft.merchant}：{itemsText(draft.items)}</p>
      <p>合计 {draft.total} 元 · 送到 {draft.address || "（未选地址）"}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        <select
          aria-label="送达地址"
          value=""
          onChange={(e) => e.target.value && onAddress(e.target.value)}
          className="rounded border border-line bg-bg px-1 text-sm"
        >
          <option value="">改地址…</option>
          {ADDRESSES.map((a) => (
            <option key={a}>{a}</option>
          ))}
        </select>
        <Btn tone="safe" disabled={!draft.address} onClick={onConfirm}>确认下单</Btn>
        <Btn onClick={onCancel}>取消</Btn>
      </div>
    </div>
  );
}

export function MeituanApp({ state, data, diff, touch, confirmPending, onEvent }: MeituanProps) {
  const [keyword, setKeyword] = useState("");
  const mt = (command: string, args: Record<string, unknown> = {}) => touch("meituan_app", { command, ...args });
  const results = read<Merchant[]>(state, "meituan.last_results", []);
  const merchantId = read(state, "meituan.merchant", "");
  const merchant = data?.merchants.find((m) => m.id === merchantId);
  const cart = read<CartItem[]>(state, "meituan.cart", []);
  const draft = read<Draft | null>(state, "meituan.draft", null);
  const orders = read<Order[]>(state, "meituan.orders", []);
  const bookings = read<Booking[]>(state, "meituan.bookings", []);

  // 大脑在等确认时，按钮就是对它的回答；否则是用户自己在屏幕上下单。
  const confirm = () => (confirmPending ? onEvent("confirm") : mt("Meituan.PlaceOrder"));
  const cancel = () => {
    if (confirmPending) onEvent("cancel");
    mt("Meituan.CancelDraft");
  };

  return (
    <div className="space-y-2">
      <form
        className="flex gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          if (keyword.trim()) mt("Meituan.Search", { Keyword: keyword.trim() });
        }}
      >
        <input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索商户、菜品"
          className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-1 text-sm"
        />
        <Btn tone="cloud" type="submit">搜索</Btn>
      </form>

      {draft && (
        <DraftCard
          draft={draft}
          confirmPending={confirmPending}
          onConfirm={confirm}
          onCancel={cancel}
          onAddress={(a) => mt("Meituan.MakeDraft", { Address: a })}
        />
      )}

      {merchant ? (
        <div className={"rounded border border-line p-2 text-sm" + hotRing(touched(diff, "meituan.merchant"))}>
          <div className="flex items-center justify-between">
            <p className="font-medium">{merchant.name}</p>
            <Btn onClick={() => mt("Meituan.Back")}>返回列表</Btn>
          </div>
          <p className="text-muted">
            {merchant.rating} 分 · 人均 {merchant.avg_price} 元 · {merchant.distance_km} 公里{merchant.rooms ? " · 有包间" : ""}
          </p>
          <ul className="mt-1 divide-y divide-line">
            {(merchant.menu ?? []).map((item) => (
              <li key={item.name} className="flex items-center justify-between py-1">
                <span>
                  {item.signature && <span className="mr-1 rounded bg-safe-bg px-1 text-xs text-safe">招牌</span>}
                  {item.name} <span className="text-muted">¥{item.price}</span>
                </span>
                <Btn aria-label={`加一份${item.name}`} onClick={() => mt("Meituan.AddToCart", { Item: item.name, Count: 1 })}>＋</Btn>
              </li>
            ))}
          </ul>
          {merchant.type !== "外卖" && (
            <Btn className="mt-1" onClick={() => mt("Meituan.Book", { People: 2, Time: "18:00" })}>订今晚 18:00 两人位</Btn>
          )}
        </div>
      ) : (
        results.length > 0 && (
          <ol className={"divide-y divide-line rounded border border-line text-sm" + hotRing(touched(diff, "meituan.last_results"))}>
            {results.map((m, i) => (
              <li key={m.id}>
                <button onClick={() => mt("Meituan.OpenMerchant", { Index: i + 1 })} className="w-full px-2 py-1 text-left">
                  <span className="text-muted">{i + 1}. </span>
                  {m.name}
                  <span className="block text-xs text-muted">
                    {m.rating} 分 · 人均 {m.avg_price} 元 · {m.distance_km} 公里
                  </span>
                </button>
              </li>
            ))}
          </ol>
        )
      )}

      {cart.length > 0 && !draft && (
        <div className={"rounded bg-bg p-2 text-sm" + hotRing(touched(diff, "meituan.cart"))}>
          <p className="text-xs text-muted">购物车</p>
          {cart.map((c) => (
            <div key={c.item} className="flex items-center justify-between">
              <span>{c.item}×{c.count} · ¥{c.price * c.count}</span>
              <Btn aria-label={`移除${c.item}`} onClick={() => mt("Meituan.RemoveFromCart", { Item: c.item })}>移除</Btn>
            </div>
          ))}
          <Btn tone="cloud" className="mt-1" onClick={() => mt("Meituan.MakeDraft", {})}>去结算</Btn>
        </div>
      )}

      {(orders.length > 0 || bookings.length > 0) && (
        <div className={"rounded border border-line p-2 text-sm" + hotRing(touched(diff, "meituan.orders"))}>
          <p className="text-xs text-muted">我的订单</p>
          {orders.map((o) => (
            <p key={o.id}>
              {o.id} {o.merchant}：<b className="text-veh">{o.status}</b>
            </p>
          ))}
          {bookings.map((b, i) => (
            <p key={i}>
              预订 {b.merchant} {b.time} {b.people} 人
            </p>
          ))}
        </div>
      )}
      {!results.length && !merchant && !draft && !orders.length && <p className="text-sm text-muted">搜索附近的餐厅，或者直接对我说“找家附近的火锅”。</p>}
    </div>
  );
}
