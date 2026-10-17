import { useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { Fees, RecordKind, Suggestion, SuggestionRequest } from './api';
import { displayAverage } from './money';
import { useLedger, useSimulationRecord } from './useLedger';
import { recordTabs, sectionGroup, type SimulationSection } from './workspace';
import { WorkspaceTabs } from './WorkspaceTabs';

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

export default function SimulationLedger({ section = 'trading', onSectionChange = () => {} }: { section?: SimulationSection; onSectionChange?: (section: SimulationSection) => void }) {
  const { query, events, create, confirm, revise, dismiss, cancel, valuation } = useLedger();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedRecord, setSelectedRecord] = useState<{ kind: RecordKind; id: string } | null>(null);
  const [recordSection, setRecordSection] = useState(section);
  const record = useSimulationRecord(selectedRecord?.kind, selectedRecord?.id);
  const [acknowledged, setAcknowledged] = useState(false);
  const [draftId, setDraftId] = useState(() => crypto.randomUUID());
  const [draft, setDraft] = useState({ code: '', exchange: 'SH' as SuggestionRequest['exchange'], name: '', side: 'buy' as SuggestionRequest['side'], quantity: '', limit_price: '', reason: '', counterargument: '', expiry: '' });
  const [inputError, setInputError] = useState('');
  const [manualOpen, setManualOpen] = useState(false);
  const [editQuantity, setEditQuantity] = useState('');
  const [editPrice, setEditPrice] = useState('');
  const [revisionId, setRevisionId] = useState(() => crypto.randomUUID());
  const ledger = query.data?.ledger;
  const currentValuation = valuation.data?.account_version === ledger?.version ? valuation.data : undefined;
  const selected = ledger?.suggestions.find((s) => s.suggestion_id === selectedId);
  const recoveredRevision = revise.isError && selected?.status === 'cancelled' ? ledger?.suggestions.find((s) => s.client_request_id === revisionId && s.replaces_suggestion_id === selectedId) : undefined;
  const busy = create.isPending || confirm.isPending || revise.isPending || dismiss.isPending || cancel.isPending;
  const actionError = create.error ?? confirm.error ?? revise.error ?? dismiss.error ?? cancel.error;

  function openRecord(kind: RecordKind, id: string) {
    setRecordSection(section); setSelectedRecord({ kind, id });
  }

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
      onSuccess: () => { setManualOpen(false); setDraftId(crypto.randomUUID()); setDraft((previous) => ({ ...previous, reason: '', counterargument: '' })); },
    });
  }

  const needsRecalculation = !!selected && (editQuantity !== String(selected.quantity) || editPrice !== selected.limit_price || selected.account_version !== ledger?.version);

  function openConfirmation(id: string) {
    const suggestion = ledger?.suggestions.find((s) => s.suggestion_id === id);
    if (!suggestion) return;
    confirm.reset(); revise.reset(); setInputError(''); setAcknowledged(false); setSelectedId(id);
    setEditQuantity(String(suggestion.quantity)); setEditPrice(suggestion.limit_price); setRevisionId(crypto.randomUUID());
  }

  function recalculate() {
    if (!selected || !ledger || busy) return;
    if (!/^[1-9][0-9]*$/.test(editQuantity) || Number(editQuantity) > 1_000_000) {
      setInputError('数量须为正整数且不超过 100 万股。'); return;
    }
    if (!/^[0-9]+(\.[0-9]{1,2})?$/.test(editPrice) || Number(editPrice) <= 0) {
      setInputError('请填写大于零且最多两位小数的限价。'); return;
    }
    setInputError(''); setAcknowledged(false);
    revise.mutate({ suggestion_id: selected.suggestion_id, suggestion_version: selected.suggestion_version,
      account_version: ledger.version, client_request_id: revisionId, quantity: Number(editQuantity), limit_price: editPrice }, {
      onSuccess: ({ result }) => { setSelectedId(result.suggestion_id); setEditQuantity(String(result.quantity)); setEditPrice(result.limit_price); setRevisionId(crypto.randomUUID()); confirm.reset(); },
    });
  }

  function suggestionCard(s: Suggestion) {
    return <article className="rounded-lg border border-border bg-card p-5 space-y-3" key={s.suggestion_id}>
      <h3 className="font-semibold">{sideName(s.side)} {s.name} · {s.code}.{s.exchange} · {statusNames[s.status]}</h3>
      <p className="text-sm tabular-nums">建议数量 {s.quantity} 股 · {s.side === 'buy' ? '价格上限' : '价格下限'} {s.limit_price} CNY · 有效至 {timestamp(s.expires_at)}（北京时间）</p>
      <p className="text-sm tabular-nums text-muted-foreground">预计现金变化 {s.estimated_cash_delta} CNY · 费用 {s.estimated_fees.total} CNY · 行情待核实</p>
      <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">理由与测算详情</summary><div className="pt-3 space-y-2">
        <p className="whitespace-pre-wrap break-words">理由：{s.reason}</p><p className="whitespace-pre-wrap break-words">反证：{s.counterargument}</p>
        <p className="tabular-nums">预计现金余额 {s.estimated_cash_after} CNY；数量变化 {s.side === 'buy' ? '+' : '−'}{s.quantity} 股。</p>
        <p className="text-xs text-muted-foreground">建议版本 {s.suggestion_version} · 账本版本 {s.account_version} · {s.fees.version}</p>
        {s.replaces_suggestion_id && <Button variant="link" size="sm" onClick={() => openRecord('suggestions', s.replaces_suggestion_id!)}>查看修改前建议</Button>}
      </div></details>
      {s.status === 'pending' && <div className="flex flex-wrap gap-3">
        <Button disabled={busy || s.account_version !== ledger?.version} onClick={() => openConfirmation(s.suggestion_id)}>核对并确认模拟{sideName(s.side)}</Button>
        <Button variant="outline" disabled={busy} onClick={() => openConfirmation(s.suggestion_id)}>修改数量 / 限价</Button>
        <Button variant="outline" disabled={busy} onClick={() => dismiss.mutate(s.suggestion_id)}>取消建议</Button>
        {s.account_version !== ledger?.version && <p className="text-sm text-muted-foreground">账本或预留已变化，请修改并重新计算后确认。</p>}
      </div>}
    </article>;
  }

  return <div>
    {query.isPending && <p role="status">正在读取当前模拟账本…</p>}
    {query.isError && <div role="alert"><p>{message(query.error)}</p><Button onClick={() => void query.refetch()}>重试读取当前账本</Button></div>}
    {ledger && <>
      <div role="tabpanel" tabIndex={0} id="simulation-panel-trading" aria-labelledby="simulation-tab-trading" hidden={section !== 'trading'} className="focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring space-y-5">
      <section id="simulation-trading" className="space-y-4" aria-label="买卖操作区">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="text-lg font-semibold">买卖操作区 <span className="ml-2 text-sm font-normal text-muted-foreground">{ledger.suggestions.filter((s) => s.status === 'pending').length} 笔待确认</span></h2><p className="mt-1 text-sm text-muted-foreground">核对建议、调整数量，逐笔确认模拟交易。</p>{ledger.orders.some((o) => o.status === 'pending') && <Button variant="link" size="sm" className="px-0" onClick={() => onSectionChange('orders')}>{ledger.orders.filter((o) => o.status === 'pending').length} 笔订单待执行 · 查看</Button>}</div>
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy} aria-expanded={manualOpen && draft.side === 'buy'} aria-controls="simulation-manual-entry" onClick={() => { update('side', 'buy'); setManualOpen(true); }}>记录模拟买入</Button>
            <Button variant="outline" disabled={busy} aria-expanded={manualOpen && draft.side === 'sell'} aria-controls="simulation-manual-entry" onClick={() => { update('side', 'sell'); setManualOpen(true); }}>记录模拟卖出</Button>
          </div>
        </div>
        <details className="text-sm text-muted-foreground"><summary className="cursor-pointer">当前能力与模拟规则</summary><div className="pt-2 space-y-2"><p>AI 自动分析转建议卡尚未接通，目前可手动录入研究建议。持仓检查报告不会自动产生买卖建议。</p><p>修改后须重新计算并手动确认；确认后才预留资源，成交仍须有效行情。订单与成交见“记录”。</p></div></details>
      </section>
      {manualOpen && <section id="simulation-manual-entry" className="rounded-lg border border-border bg-card p-6 space-y-4" aria-label="记录手动建议">
        <div className="flex justify-between gap-3"><h2 className="text-lg font-semibold">记录模拟{sideName(draft.side)}建议</h2><Button variant="ghost" disabled={busy} onClick={() => setManualOpen(false)}>关闭录入</Button></div>
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
      </section>}
      <section className="space-y-4" aria-label="模拟建议">
        <h2 className="text-lg font-semibold">待确认建议</h2>
        {!ledger.suggestions.some((s) => s.status === 'pending') && <p className="text-sm text-muted-foreground">暂无待确认建议。可点击“记录模拟买入 / 卖出”录入；保存后仍需逐笔确认。</p>}
        {ledger.suggestions.filter((s) => s.status === 'pending').map(suggestionCard)}
      </section>

      </div>
      <div role="tabpanel" tabIndex={0} id="simulation-panel-holdings" aria-labelledby="simulation-tab-holdings" hidden={section !== 'holdings'} className="focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
      <section className="rounded-lg border border-border bg-card p-5 sm:p-6 space-y-5" aria-label="当前模拟账本">
        <div className="flex flex-wrap justify-between gap-3"><h2 className="text-lg font-semibold">当前模拟账本 · 版本 {ledger.version}</h2><Button variant="outline" size="sm" onClick={() => void query.refetch()} disabled={query.isFetching}>刷新账本</Button></div>
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          {([['当前现金', ledger.cash], ['可用现金', ledger.available_cash], ['订单预留', ledger.reserved_cash], ['起算后已实现盈亏', ledger.realized_pnl]] as const).map(([label, value]) =>
            <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="tabular-nums break-all">{value} CNY</dd></div>)}
        </dl>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground">行情只在手动刷新时查询；报价用于本地估值，不用于模拟成交。{ledger.sellable_basis}。</p>
          <Button variant="outline" size="sm" onClick={() => valuation.mutate()} disabled={valuation.isPending || query.isFetching || busy || ledger.positions.length === 0}>
            {valuation.isPending ? '正在查询行情…' : '刷新行情并计算'}
          </Button>
        </div>
        {valuation.isError && <p role="alert" className="text-sm text-destructive">{message(valuation.error)} · 可手动重试。</p>}
        {valuation.data && !currentValuation && <p role="status" className="text-sm text-muted-foreground">持仓已变化，请重新刷新行情。</p>}
        {currentValuation && <p className="text-xs text-muted-foreground">本次取数时间：{timestamp(currentValuation.fetched_at)}（北京时间）；实时报价按现价 × 持仓数量 − 账本总成本计算，非实时报价明确标为估算。</p>}
        {ledger.positions.length ? <div className="overflow-x-auto"><table className="w-full min-w-[960px] text-sm text-left">
          <caption className="sr-only">持仓估值</caption>
          <thead><tr>{['证券', '持有数量', '可卖余额', '总成本 CNY', '显示均价 CNY', '最新报价', '市值 CNY', '浮动盈亏 CNY'].map((label) => <th className="p-2 border-b" key={label}>{label}</th>)}</tr></thead>
          <tbody>{ledger.positions.map((p) => {
            const positionValuation = currentValuation?.positions.find((item) => item.code === p.code && item.exchange === p.exchange);
            const quote = positionValuation?.current_quote;
            const pnl = positionValuation?.realtime_pnl;
            const pnlAvailable = (pnl?.status === 'calculated' || pnl?.status === 'estimated') && pnl.unrealized_pnl !== null;
            const freshnessLabel = quote?.freshness === 'fresh' ? '实时' : quote?.freshness === 'future' ? '时间异常' : pnl?.status === 'estimated' ? '最近可用估算' : '报价过旧';
            return <tr key={`${p.code}.${p.exchange}`}>
              <td className="p-2">{p.name}<br />{p.code}.{p.exchange}</td>
              <td className="p-2 tabular-nums">{p.quantity}</td>
              <td className="p-2 tabular-nums">{p.available_quantity}</td>
              <td className="p-2 tabular-nums">{p.total_cost}</td>
              <td className="p-2 tabular-nums">{displayAverage(p.total_cost, p.quantity)}</td>
              <td className="p-2 tabular-nums">{quote ? <><span>{quote.price}</span><br /><span className="text-xs text-muted-foreground">{freshnessLabel} · {timestamp(quote.as_of)}</span></> : '待核实'}</td>
              <td className="p-2 tabular-nums">{pnlAvailable && pnl?.market_value !== null ? pnl.market_value : '待核实'}</td>
              <td className={`p-2 tabular-nums font-medium ${pnlAvailable ? (pnl.unrealized_pnl!.startsWith('-') ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-600 dark:text-red-400') : ''}`} title={pnl?.reason ?? undefined}>
                {pnlAvailable ? `${pnl.unrealized_pnl!.startsWith('-') ? '' : '+'}${pnl.unrealized_pnl}` : (pnl ? '待核实' : '未刷新')}
              </td>
            </tr>;
          })}</tbody>
        </table></div> : <p className="text-sm">当前无持仓。</p>}
        <details className="text-sm"><summary className="cursor-pointer text-muted-foreground">模拟费用与规则</summary><div className="pt-2"><FeeNote fees={ledger.fees} /></div></details>
      </section>

      </div>
      <div role="tabpanel" tabIndex={0} id="simulation-panel-records" aria-labelledby="simulation-tab-records" hidden={sectionGroup(section) !== 'records'} className="focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring space-y-5">
      <WorkspaceTabs prefix="simulation-record" label="记录类型" tabs={recordTabs} value={section} onChange={onSectionChange} />
      <div role="tabpanel" tabIndex={0} id="simulation-record-panel-orders" aria-labelledby="simulation-record-tab-orders" hidden={section !== 'orders'} className="focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
      <section className="space-y-4" aria-label="模拟订单" id="simulation-orders">
        <h2 className="text-lg font-semibold">模拟订单</h2>
        {!ledger.orders.length && <p className="text-sm text-muted-foreground">暂无订单。只有你逐笔确认后才会建立订单。</p>}
        {ledger.orders.map((o) => <article className="rounded-lg border border-border bg-card p-5 space-y-3" key={o.order_id}>
          <h3 className="font-semibold">{sideName(o.side)} {o.name} · {o.quantity} 股 · {o.status === 'pending' ? '待执行 · 等待有效行情' : statusNames[o.status]}</h3>
          <p className="text-sm tabular-nums">{o.code}.{o.exchange} · 限价 {o.limit_price} CNY · 确认于 {timestamp(o.confirmed_at)}</p>
          <details className="text-xs text-muted-foreground break-all"><summary className="cursor-pointer">订单标识</summary><p className="pt-2">订单 {o.order_id} · 建议 {o.suggestion_id}</p></details>
          {o.status === 'pending' && <><p className="text-sm">预留现金 {o.reserved_cash} CNY；卖出订单预留对应数量。行情与日历未接通，当前不会成交。有效至 {timestamp(o.expires_at)}。</p><Button variant="outline" disabled={busy} onClick={() => cancel.mutate(o.order_id)}>取消模拟订单</Button></>}
        </article>)}
      </section>

      </div>
      <div role="tabpanel" tabIndex={0} id="simulation-record-panel-fills" aria-labelledby="simulation-record-tab-fills" hidden={section !== 'fills'} className="focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
      <section className="space-y-4" aria-label="模拟成交记录" id="simulation-fills">
        <h2 className="text-lg font-semibold">模拟成交记录</h2>
        {!ledger.fills.length && <p className="text-sm text-muted-foreground">暂无模拟成交。未确认、取消、到期或缺少有效行情都不会记账。</p>}
        {ledger.fills.map((f) => <article className="rounded-lg border border-border bg-card p-5 space-y-2" key={f.fill_id}>
          <h3 className="font-semibold">{sideName(f.side)} {f.name} · {f.quantity} 股 × {f.price} CNY{f.quote.source_kind === 'test' && ' · 虚构测试行情'}</h3>
          <p className="text-sm tabular-nums">费用 {f.fees.total} CNY · 现金变化 {f.cash_delta} CNY · 成交后现金 {f.cash_after} CNY · 本笔已实现盈亏 {f.realized_pnl} CNY</p>
          <p className="text-xs text-muted-foreground break-all">{f.quote.source} · 报价时间 {timestamp(f.quote.as_of)} · 证据 {f.quote.evidence_id} · {f.fees.version}<br />成交 {f.fill_id} · 订单 {f.order_id}</p>
        </article>)}
      </section>

      </div>
      <div role="tabpanel" tabIndex={0} id="simulation-record-panel-history" aria-labelledby="simulation-record-tab-history" hidden={section !== 'history'} className="focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
        <section id="simulation-history" aria-label="历史建议" className="space-y-4"><h2 className="text-lg font-semibold">历史建议</h2>
          <p className="text-sm text-muted-foreground">已确认、取消和到期的建议保留原始内容；更早记录可从操作审计回查。</p>
          {!ledger.suggestions.some((s) => s.status !== 'pending') && <p className="text-sm text-muted-foreground">暂无历史建议。</p>}
          {ledger.suggestions.filter((s) => s.status !== 'pending').map(suggestionCard)}
        </section>
      </div>
      <div role="tabpanel" tabIndex={0} id="simulation-record-panel-audit" aria-labelledby="simulation-record-tab-audit" hidden={section !== 'audit'} className="focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring">
      <section className="space-y-3" aria-label="模拟操作审计" id="simulation-audit">
        <h2 className="text-lg font-semibold">操作审计与现金变化</h2>
        <p className="text-sm text-muted-foreground">期初现金与原始持仓见不可变期初快照。以下记录起算后的动作；成本采用移动加权平均法，历史未知费用不补记。</p>
        {events.isPending && <p role="status">正在读取操作审计…</p>}
        {events.isError && <div role="alert">{message(events.error)}<Button variant="outline" onClick={() => void events.refetch()}>重试读取审计</Button></div>}
        {events.data?.pages.flatMap((p) => p.events).map((e) => <div className="border-b border-border pb-3 text-sm" key={e.event_id}>
          <p>{timestamp(e.created_at)} · {e.payload.replaces_suggestion_id ? '修改建议并重新计算' : (statusNames[e.event_type] ?? e.event_type)} · 现金变化 {e.cash_delta} CNY</p>
          <p className="text-xs text-muted-foreground break-all">审计 #{e.event_id} · {e.payload.order_id ? `订单 ${e.payload.order_id}` : `建议 ${e.payload.suggestion_id ?? ''}`}{e.payload.account_version && ` · 账本版本 ${e.payload.account_version}`}</p>
          <div className="flex flex-wrap gap-2 mt-2">
            {e.payload.suggestion_id && <Button variant="outline" size="sm" onClick={() => openRecord('suggestions', e.payload.suggestion_id!)}>查看建议</Button>}
            {e.payload.order_id && <Button variant="outline" size="sm" onClick={() => openRecord('orders', e.payload.order_id!)}>查看订单</Button>}
            {e.payload.fill_id && <Button variant="outline" size="sm" onClick={() => openRecord('fills', e.payload.fill_id!)}>查看成交</Button>}
          </div>
        </div>)}
        {events.hasNextPage && <Button variant="outline" disabled={events.isFetchingNextPage} onClick={() => void events.fetchNextPage()}>读取更早审计</Button>}
      </section>
      </div></div>
    </>}
    {actionError && <p role="alert">{message(actionError)}</p>}
    <Dialog open={section === 'trading' && !!selectedId && !!selected} onOpenChange={(open) => { if (!open && !confirm.isPending && !revise.isPending) setSelectedId(null); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader><DialogTitle>确认本笔模拟{selected && sideName(selected.side)}</DialogTitle><DialogDescription>确认只建立模拟订单并预留资源；之后的有效行情满足条件才可模拟成交。</DialogDescription></DialogHeader>
        {selected && <div className="space-y-4 text-sm">
          <p className="font-semibold">{selected.name} · {selected.code}.{selected.exchange} · {selected.quantity} 股 · {selected.side === 'buy' ? '价格上限' : '价格下限'} {selected.limit_price} CNY</p>
          <div className="grid sm:grid-cols-2 gap-3">
            <label>本次数量（股）<Input aria-label="本次数量" inputMode="numeric" value={editQuantity} disabled={busy || selected.status !== 'pending'} onChange={(e) => { setEditQuantity(e.target.value); setAcknowledged(false); setRevisionId(crypto.randomUUID()); setInputError(''); }} /></label>
            <label>本次{selected.side === 'buy' ? '买入上限' : '卖出下限'}（CNY）<Input aria-label="本次限价" inputMode="decimal" value={editPrice} disabled={busy || selected.status !== 'pending'} onChange={(e) => { setEditPrice(e.target.value); setAcknowledged(false); setRevisionId(crypto.randomUUID()); setInputError(''); }} /></label>
          </div>
          {needsRecalculation && <p role="status">数量、限价或账本已变化，请先重新计算并保存，再核对费用和确认。原建议保留在历史记录。</p>}
          <Button variant="outline" disabled={busy || !needsRecalculation || selected.status !== 'pending'} onClick={recalculate}>{revise.isPending ? '正在重新计算…' : '重新计算并保存建议'}</Button>
          {inputError && <p role="alert">{inputError}</p>}
          {revise.isError && <p role="alert">{message(revise.error)}</p>}
          {recoveredRevision && <div role="status"><p>重新计算已保存，可打开新版本继续核对；仍须手动确认。</p><Button variant="outline" disabled={busy} onClick={() => openConfirmation(recoveredRevision.suggestion_id)}>查看已保存的新建议</Button></div>}
          {selected.status !== 'pending' && !recoveredRevision && <p role="alert">此建议已结束，请返回查看最新待确认卡片。</p>}
          <p>有效至 {timestamp(selected.expires_at)}（北京时间）；建议版本 {selected.suggestion_version}，账本版本 {selected.account_version}。</p>
          <p className="whitespace-pre-wrap break-words">理由：{selected.reason}</p><p className="whitespace-pre-wrap break-words">反证：{selected.counterargument}</p>
          <FeeNote fees={selected.fees} />
          <p>限价估算费用 {selected.estimated_fees.total} CNY，现金变化 {selected.estimated_cash_delta} CNY，数量变化 {selected.side === 'buy' ? '+' : '−'}{selected.quantity} 股。</p>
          <label className="flex items-start gap-2"><input type="checkbox" checked={acknowledged} disabled={busy || needsRecalculation || selected.status !== 'pending'} onChange={(e) => setAcknowledged(e.target.checked)} />我已核对本笔证券、方向、数量、限价与费用，确认仅执行模拟交易。</label>
          {ledger && selected.account_version !== ledger.version && <p role="alert">账本或预留已变化，请点击“重新计算并保存建议”后再次核对。</p>}
          {confirm.isError && <p role="alert">{message(confirm.error)}</p>}
          <DialogFooter>
            <Button variant="outline" disabled={busy} onClick={() => setSelectedId(null)}>返回核对</Button>
            <Button disabled={!acknowledged || needsRecalculation || busy || selected.status !== 'pending' || selected.account_version !== ledger?.version} onClick={() => {
              if (confirm.isPending) return;
              confirm.mutate({ suggestion_id: selected.suggestion_id, suggestion_version: selected.suggestion_version, account_version: selected.account_version, acknowledge_simulation: true }, { onSuccess: () => setSelectedId(null) });
            }}>{confirm.isPending ? '正在确认…' : `确认模拟${sideName(selected.side)}`}</Button>
          </DialogFooter>
        </div>}
      </DialogContent>
    </Dialog>
    <Dialog open={!!selectedRecord && section === recordSection} onOpenChange={(open) => { if (!open) setSelectedRecord(null); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader><DialogTitle>模拟审计记录</DialogTitle><DialogDescription>可读取历史原始建议、订单和成交，不受首页终态记录数量限制。</DialogDescription></DialogHeader>
        {record.isPending && <p role="status">正在读取记录…</p>}
        {record.isError && <div role="alert">{message(record.error)}<Button variant="outline" onClick={() => void record.refetch()}>重试读取记录</Button></div>}
        {record.data && <div className="space-y-3 text-sm break-words">
          <h3 className="font-semibold">{sideName(record.data.result.side)} {record.data.result.name} · {record.data.result.code}.{record.data.result.exchange} · {record.data.result.quantity} 股</h3>
          {'fill_id' in record.data.result ? <>
            <p>成交价 {record.data.result.price} CNY · 费用 {record.data.result.fees.total} CNY · 现金变化 {record.data.result.cash_delta} CNY</p>
            <p>报价来源 {record.data.result.quote.source}（{record.data.result.quote.source_kind === 'test' ? '虚构测试' : '供应商'}），时间 {timestamp(record.data.result.quote.as_of)}；证据 {record.data.result.quote.evidence_id}。</p>
            <Button variant="outline" onClick={() => { const r = record.data?.result; if (r && 'fill_id' in r) openRecord('orders', r.order_id); }}>查看来源订单</Button>
          </> : <>
            <p>限价 {record.data.result.limit_price} CNY · 状态 {statusNames[record.data.result.status]} · 建议版本 {record.data.result.suggestion_version} · 账本版本 {record.data.result.account_version}</p>
            <p className="whitespace-pre-wrap">理由：{record.data.result.reason}</p><p className="whitespace-pre-wrap">反证：{record.data.result.counterargument}</p>
            <p>有效至 {timestamp(record.data.result.expires_at)}（北京时间）。</p>
            <FeeNote fees={record.data.result.fees} />
            {'order_id' in record.data.result && <Button variant="outline" onClick={() => { const r = record.data?.result; if (r && 'suggestion_id' in r) openRecord('suggestions', r.suggestion_id); }}>查看来源建议</Button>}
          </>}
          <p className="text-xs text-muted-foreground break-all">记录 {selectedRecord?.id}</p>
        </div>}
      </DialogContent>
    </Dialog>
  </div>;
}
