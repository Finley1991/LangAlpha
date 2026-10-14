import { useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { Fees, RecordKind, SuggestionRequest } from './api';
import { displayAverage } from './money';
import { useLedger, useSimulationRecord } from './useLedger';

const statusNames: Record<string, string> = {
  pending: '待确认', confirmed: '已确认', cancelled: '已取消', expired: '已到期', filled: '已模拟成交',
  suggested: '保存建议', suggestion_cancelled: '取消建议', suggestion_expired: '建议到期',
  order_cancelled: '取消订单', order_expired: '订单到期',
};
const sideName = (side: 'buy' | 'sell') => side === 'buy' ? '买入' : '卖出';
const timestamp = (value: string) => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium', hour12: false,
}).format(new Date(value));
const message = (error: unknown) => error instanceof Error ? error.message : '暂时无法完成，请重试。';

function FeeNote({ fees }: { fees: Fees }) {
  return <p className="text-sm text-muted-foreground">
    {fees.version} · 佣金率 {fees.commission_rate}（每笔最低 {fees.minimum_commission} 元），卖出模拟税费率 {fees.sell_tax_rate}，
    双向模拟过户费率 {fees.transfer_rate}，不利滑点 {fees.slippage_bps} 基点。{fees.description}
  </p>;
}

export default function SimulationLedger() {
  const { query, events, create, confirm, dismiss, cancel } = useLedger();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedRecord, setSelectedRecord] = useState<{ kind: RecordKind; id: string } | null>(null);
  const record = useSimulationRecord(selectedRecord?.kind, selectedRecord?.id);
  const [acknowledged, setAcknowledged] = useState(false);
  const [draftId, setDraftId] = useState(() => crypto.randomUUID());
  const [draft, setDraft] = useState({ code: '', exchange: 'SH' as SuggestionRequest['exchange'], name: '', side: 'buy' as SuggestionRequest['side'], quantity: '', limit_price: '', reason: '', counterargument: '', expiry: '' });
  const [inputError, setInputError] = useState('');
  const ledger = query.data?.ledger;
  const selected = ledger?.suggestions.find((s) => s.suggestion_id === selectedId);
  const busy = create.isPending || confirm.isPending || dismiss.isPending || cancel.isPending;
  const actionError = create.error ?? confirm.error ?? dismiss.error ?? cancel.error;

  function update(field: keyof typeof draft, value: string) {
    setDraft((previous) => ({ ...previous, [field]: value }));
    setDraftId(crypto.randomUUID());
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !ledger) return;
    setInputError('');
    if (!/^[1-9][0-9]*$/.test(draft.quantity) || Number(draft.quantity) > 1_000_000) {
      setInputError('数量须为正整数且不超过 100 万股。'); return;
    }
    const expiry = new Date(draft.expiry);
    if (!Number.isFinite(expiry.getTime())) { setInputError('请填写建议有效期。'); return; }
    const { expiry: _expiry, ...fields } = draft;
    create.mutate({ ...fields, quantity: Number(draft.quantity), expires_at: expiry.toISOString(), client_request_id: draftId, account_version: ledger.version }, {
      onSuccess: () => { setDraftId(crypto.randomUUID()); setDraft((previous) => ({ ...previous, reason: '', counterargument: '' })); },
    });
  }

  function openConfirmation(id: string) {
    confirm.reset(); setAcknowledged(false); setSelectedId(id);
  }

  return <div className="space-y-7">
    {query.isPending && <p role="status">正在读取当前模拟账本…</p>}
    {query.isError && <div role="alert"><p>{message(query.error)}</p><Button onClick={() => void query.refetch()}>重试读取当前账本</Button></div>}
    {ledger && <>
      <section className="rounded-lg border border-border bg-card p-6 space-y-5" aria-label="当前模拟账本">
        <div className="flex flex-wrap justify-between gap-3"><h2 className="text-lg font-semibold">当前模拟账本 · 版本 {ledger.version}</h2><Button variant="outline" size="sm" onClick={() => void query.refetch()} disabled={query.isFetching}>刷新账本</Button></div>
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          {([['当前现金', ledger.cash], ['可用现金', ledger.available_cash], ['订单预留', ledger.reserved_cash], ['起算后已实现盈亏', ledger.realized_pnl]] as const).map(([label, value]) =>
            <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="tabular-nums break-all">{value} CNY</dd></div>)}
        </dl>
        <p className="text-sm text-muted-foreground">行情待核实，暂不计算市值、浮动收益和总资产。{ledger.sellable_basis}。</p>
        {ledger.positions.length ? <div className="overflow-x-auto"><table className="w-full text-sm text-left">
          <caption className="sr-only">当前数量、可卖余额与精确成本</caption>
          <thead><tr>{['证券', '持有数量', '可卖余额', '总成本 CNY', '显示均价 CNY'].map((label) => <th className="p-2 border-b" key={label}>{label}</th>)}</tr></thead>
          <tbody>{ledger.positions.map((p) => <tr key={`${p.code}.${p.exchange}`}><td className="p-2">{p.name}<br />{p.code}.{p.exchange}</td><td className="p-2 tabular-nums">{p.quantity}</td><td className="p-2 tabular-nums">{p.available_quantity}</td><td className="p-2 tabular-nums">{p.total_cost}</td><td className="p-2 tabular-nums">{displayAverage(p.total_cost, p.quantity)}</td></tr>)}</tbody>
        </table></div> : <p className="text-sm">当前无持仓。</p>}
        <FeeNote fees={ledger.fees} />
      </section>

      <section className="rounded-lg border border-border bg-card p-6 space-y-4" aria-label="记录手动建议">
        <h2 className="text-lg font-semibold">记录手动研究建议</h2>
        <p className="text-sm text-muted-foreground">保存卡片后仍需逐笔确认。限价由你填写，参考行情待核实；当前仅覆盖沪深主板普通 A 股，AI 日报与通知后续接入。</p>
        <form onSubmit={submit} className="space-y-4">
          <fieldset disabled={busy} className="space-y-4">
            <legend className="sr-only">手动建议参数</legend>
            <div className="grid sm:grid-cols-3 gap-4">
              <label className="text-sm">六位代码<Input aria-label="建议证券代码" pattern="[0-9]{6}" required value={draft.code} onChange={(e) => update('code', e.target.value)} /></label>
              <label className="text-sm">交易所<select aria-label="建议交易所" className="h-9 w-full rounded-md border border-input bg-input px-2" value={draft.exchange} onChange={(e) => update('exchange', e.target.value)}><option value="SH">上海 SH</option><option value="SZ">深圳 SZ</option></select></label>
              <label className="text-sm">名称<Input aria-label="建议证券名称" maxLength={100} required value={draft.name} onChange={(e) => update('name', e.target.value)} /></label>
              <label className="text-sm">方向<select aria-label="建议方向" className="h-9 w-full rounded-md border border-input bg-input px-2" value={draft.side} onChange={(e) => update('side', e.target.value)}><option value="buy">模拟买入</option><option value="sell">模拟卖出</option></select></label>
              <label className="text-sm">数量（股）<Input aria-label="建议数量" inputMode="numeric" pattern="[1-9][0-9]*" required value={draft.quantity} onChange={(e) => update('quantity', e.target.value)} /></label>
              <label className="text-sm">{draft.side === 'buy' ? '买入价格上限' : '卖出价格下限'}（CNY）<Input aria-label="建议限价" inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" required value={draft.limit_price} onChange={(e) => update('limit_price', e.target.value)} /></label>
              <label className="text-sm sm:col-span-3">有效期（本机时区）<Input aria-label="建议有效期" type="datetime-local" required value={draft.expiry} onChange={(e) => update('expiry', e.target.value)} /></label>
            </div>
            <label className="block text-sm">建议理由<textarea aria-label="建议理由" required maxLength={2000} className="mt-1 min-h-20 w-full rounded-md border border-input bg-input p-3" value={draft.reason} onChange={(e) => update('reason', e.target.value)} /></label>
            <label className="block text-sm">反证与不确定性<textarea aria-label="反证与不确定性" required maxLength={2000} className="mt-1 min-h-20 w-full rounded-md border border-input bg-input p-3" value={draft.counterargument} onChange={(e) => update('counterargument', e.target.value)} /></label>
            <Button type="submit">{create.isPending ? '正在保存建议…' : '保存待确认建议'}</Button>
          </fieldset>
          {inputError && <p role="alert">{inputError}</p>}
        </form>
      </section>

      <section className="space-y-4" aria-label="模拟建议">
        <h2 className="text-lg font-semibold">建议卡片</h2>
        {!ledger.suggestions.length && <p className="text-sm text-muted-foreground">暂无建议。保存建议不会预留资金或改变仓位。</p>}
        {ledger.suggestions.map((s) => <article className="rounded-lg border border-border bg-card p-5 space-y-3" key={s.suggestion_id}>
          <h3 className="font-semibold">{sideName(s.side)} {s.name} · {s.code}.{s.exchange} · {statusNames[s.status]}</h3>
          <p className="text-sm tabular-nums">{s.quantity} 股 · {s.side === 'buy' ? '价格上限' : '价格下限'} {s.limit_price} CNY · 有效至 {timestamp(s.expires_at)}（北京时间）</p>
          <p className="text-sm whitespace-pre-wrap break-words">理由：{s.reason}</p><p className="text-sm whitespace-pre-wrap break-words">反证：{s.counterargument}</p>
          <p className="text-sm tabular-nums">按限价估算：费用 {s.estimated_fees.total} CNY，现金变化 {s.estimated_cash_delta} CNY，现金余额 {s.estimated_cash_after} CNY；数量变化 {s.side === 'buy' ? '+' : '−'}{s.quantity} 股。</p>
          <p className="text-xs text-muted-foreground">建议版本 {s.suggestion_version} · 账本版本 {s.account_version} · 参考行情待核实 · {s.fees.version}</p>
          {s.status === 'pending' && <div className="flex flex-wrap gap-3">
            <Button disabled={busy || s.account_version !== ledger.version} onClick={() => openConfirmation(s.suggestion_id)}>核对并确认模拟{sideName(s.side)}</Button>
            <Button variant="outline" disabled={busy} onClick={() => dismiss.mutate(s.suggestion_id)}>取消建议</Button>
            {s.account_version !== ledger.version && <p className="text-sm text-muted-foreground">账本或预留已变化，请重新生成建议。</p>}
          </div>}
        </article>)}
        <p className="text-xs text-muted-foreground">建议、订单及成交各展示最近 {ledger.history_limit} 条；操作审计可向前翻页。</p>
      </section>

      <section className="space-y-4" aria-label="模拟订单">
        <h2 className="text-lg font-semibold">模拟订单</h2>
        {!ledger.orders.length && <p className="text-sm text-muted-foreground">暂无订单。只有你逐笔确认后才会建立订单。</p>}
        {ledger.orders.map((o) => <article className="rounded-lg border border-border bg-card p-5 space-y-3" key={o.order_id}>
          <h3 className="font-semibold">{sideName(o.side)} {o.name} · {o.quantity} 股 · {o.status === 'pending' ? '待执行 · 等待有效行情' : statusNames[o.status]}</h3>
          <p className="text-sm tabular-nums">{o.code}.{o.exchange} · 限价 {o.limit_price} CNY · 确认于 {timestamp(o.confirmed_at)}</p>
          <p className="text-xs text-muted-foreground break-all">订单 {o.order_id} · 建议 {o.suggestion_id}</p>
          {o.status === 'pending' && <><p className="text-sm">预留现金 {o.reserved_cash} CNY；卖出订单预留对应数量。行情与日历未接通，当前不会成交。有效至 {timestamp(o.expires_at)}。</p><Button variant="outline" disabled={busy} onClick={() => cancel.mutate(o.order_id)}>取消模拟订单</Button></>}
        </article>)}
      </section>

      <section className="space-y-4" aria-label="模拟成交记录">
        <h2 className="text-lg font-semibold">模拟成交记录</h2>
        {!ledger.fills.length && <p className="text-sm text-muted-foreground">暂无模拟成交。未确认、取消、到期或缺少有效行情都不会记账。</p>}
        {ledger.fills.map((f) => <article className="rounded-lg border border-border bg-card p-5 space-y-2" key={f.fill_id}>
          <h3 className="font-semibold">{sideName(f.side)} {f.name} · {f.quantity} 股 × {f.price} CNY{f.quote.source_kind === 'test' && ' · 虚构测试行情'}</h3>
          <p className="text-sm tabular-nums">费用 {f.fees.total} CNY · 现金变化 {f.cash_delta} CNY · 成交后现金 {f.cash_after} CNY · 本笔已实现盈亏 {f.realized_pnl} CNY</p>
          <p className="text-xs text-muted-foreground break-all">{f.quote.source} · 报价时间 {timestamp(f.quote.as_of)} · 证据 {f.quote.evidence_id} · {f.fees.version}<br />成交 {f.fill_id} · 订单 {f.order_id}</p>
        </article>)}
      </section>

      <section className="space-y-3" aria-label="模拟操作审计">
        <h2 className="text-lg font-semibold">操作审计与现金变化</h2>
        <p className="text-sm text-muted-foreground">期初现金与原始持仓见不可变期初快照。以下记录起算后的动作；成本采用移动加权平均法，历史未知费用不补记。</p>
        {events.isPending && <p role="status">正在读取操作审计…</p>}
        {events.isError && <div role="alert">{message(events.error)}<Button variant="outline" onClick={() => void events.refetch()}>重试读取审计</Button></div>}
        {events.data?.pages.flatMap((p) => p.events).map((e) => <div className="border-b border-border pb-3 text-sm" key={e.event_id}>
          <p>{timestamp(e.created_at)} · {statusNames[e.event_type] ?? e.event_type} · 现金变化 {e.cash_delta} CNY</p>
          <p className="text-xs text-muted-foreground break-all">审计 #{e.event_id} · {e.payload.order_id ? `订单 ${e.payload.order_id}` : `建议 ${e.payload.suggestion_id ?? ''}`}{e.payload.account_version && ` · 账本版本 ${e.payload.account_version}`}</p>
          <div className="flex flex-wrap gap-2 mt-2">
            {e.payload.suggestion_id && <Button variant="outline" size="sm" onClick={() => setSelectedRecord({ kind: 'suggestions', id: e.payload.suggestion_id! })}>查看建议</Button>}
            {e.payload.order_id && <Button variant="outline" size="sm" onClick={() => setSelectedRecord({ kind: 'orders', id: e.payload.order_id! })}>查看订单</Button>}
            {e.payload.fill_id && <Button variant="outline" size="sm" onClick={() => setSelectedRecord({ kind: 'fills', id: e.payload.fill_id! })}>查看成交</Button>}
          </div>
        </div>)}
        {events.hasNextPage && <Button variant="outline" disabled={events.isFetchingNextPage} onClick={() => void events.fetchNextPage()}>读取更早审计</Button>}
      </section>
    </>}
    {actionError && <p role="alert">{message(actionError)}</p>}
    <Dialog open={!!selectedId && !!selected} onOpenChange={(open) => { if (!open && !confirm.isPending) setSelectedId(null); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader><DialogTitle>确认本笔模拟{selected && sideName(selected.side)}</DialogTitle><DialogDescription>确认只建立模拟订单并预留资源；之后的有效行情满足条件才可模拟成交。</DialogDescription></DialogHeader>
        {selected && <div className="space-y-4 text-sm">
          <p className="font-semibold">{selected.name} · {selected.code}.{selected.exchange} · {selected.quantity} 股 · {selected.side === 'buy' ? '价格上限' : '价格下限'} {selected.limit_price} CNY</p>
          <p>有效至 {timestamp(selected.expires_at)}（北京时间）；建议版本 {selected.suggestion_version}，账本版本 {selected.account_version}。</p>
          <FeeNote fees={selected.fees} />
          <p>限价估算费用 {selected.estimated_fees.total} CNY，现金变化 {selected.estimated_cash_delta} CNY，数量变化 {selected.side === 'buy' ? '+' : '−'}{selected.quantity} 股。</p>
          <label className="flex items-start gap-2"><input type="checkbox" checked={acknowledged} disabled={confirm.isPending} onChange={(e) => setAcknowledged(e.target.checked)} />我已核对本笔证券、方向、数量、限价与费用，确认仅执行模拟交易。</label>
          {ledger && selected.account_version !== ledger.version && <p role="alert">账本或预留已变化，请关闭并重新生成建议。</p>}
          {confirm.isError && <p role="alert">{message(confirm.error)}</p>}
          <DialogFooter>
            <Button variant="outline" disabled={confirm.isPending} onClick={() => setSelectedId(null)}>返回核对</Button>
            <Button disabled={!acknowledged || busy || selected.status !== 'pending' || selected.account_version !== ledger?.version} onClick={() => {
              if (confirm.isPending) return;
              confirm.mutate({ suggestion_id: selected.suggestion_id, suggestion_version: selected.suggestion_version, account_version: selected.account_version, acknowledge_simulation: true }, { onSuccess: () => setSelectedId(null) });
            }}>{confirm.isPending ? '正在确认…' : `确认模拟${sideName(selected.side)}`}</Button>
          </DialogFooter>
        </div>}
      </DialogContent>
    </Dialog>
    <Dialog open={!!selectedRecord} onOpenChange={(open) => { if (!open) setSelectedRecord(null); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader><DialogTitle>模拟审计记录</DialogTitle><DialogDescription>可读取历史原始建议、订单和成交，不受首页最近 100 条的限制。</DialogDescription></DialogHeader>
        {record.isPending && <p role="status">正在读取记录…</p>}
        {record.isError && <div role="alert">{message(record.error)}<Button variant="outline" onClick={() => void record.refetch()}>重试读取记录</Button></div>}
        {record.data && <div className="space-y-3 text-sm break-words">
          <h3 className="font-semibold">{sideName(record.data.result.side)} {record.data.result.name} · {record.data.result.code}.{record.data.result.exchange} · {record.data.result.quantity} 股</h3>
          {'fill_id' in record.data.result ? <>
            <p>成交价 {record.data.result.price} CNY · 费用 {record.data.result.fees.total} CNY · 现金变化 {record.data.result.cash_delta} CNY</p>
            <p>报价来源 {record.data.result.quote.source}（{record.data.result.quote.source_kind === 'test' ? '虚构测试' : '供应商'}），时间 {timestamp(record.data.result.quote.as_of)}；证据 {record.data.result.quote.evidence_id}。</p>
            <Button variant="outline" onClick={() => { const r = record.data?.result; if (r && 'fill_id' in r) setSelectedRecord({ kind: 'orders', id: r.order_id }); }}>查看来源订单</Button>
          </> : <>
            <p>限价 {record.data.result.limit_price} CNY · 状态 {statusNames[record.data.result.status]} · 建议版本 {record.data.result.suggestion_version} · 账本版本 {record.data.result.account_version}</p>
            <p className="whitespace-pre-wrap">理由：{record.data.result.reason}</p><p className="whitespace-pre-wrap">反证：{record.data.result.counterargument}</p>
            <p>有效至 {timestamp(record.data.result.expires_at)}（北京时间）。</p>
            <FeeNote fees={record.data.result.fees} />
            {'order_id' in record.data.result && <Button variant="outline" onClick={() => { const r = record.data?.result; if (r && 'suggestion_id' in r) setSelectedRecord({ kind: 'suggestions', id: r.suggestion_id }); }}>查看来源建议</Button>}
          </>}
          <p className="text-xs text-muted-foreground break-all">记录 {selectedRecord?.id}</p>
        </div>}
      </DialogContent>
    </Dialog>
  </div>;
}
