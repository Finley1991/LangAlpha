import { useState, type FormEvent } from 'react';
import { Link } from 'react-router';
import { ApiError } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useOpening } from './useOpening';
import type { OpeningHolding } from './api';
import { displayAverage } from './money';
import SimulationLedger from './SimulationLedger';
import SimulationReports from './SimulationReports';

interface DraftHolding { id: string; code: string; exchange: OpeningHolding['exchange']; name: string; quantity: string; total_cost: string; purchased_on: string }
const emptyHolding = (): DraftHolding => ({ id: crypto.randomUUID(), code: '', exchange: 'SH', name: '', quantity: '', total_cost: '', purchased_on: '' });

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return '已有期初账本，不能覆盖或累计仓位。请刷新页面查看已保存的快照。';
  if (error instanceof ApiError && error.status === 422) return '请检查日期、数量和金额格式；买入日期不能晚于起算日期，现金最多两位小数，成本最多八位小数。';
  return error instanceof Error ? error.message : '暂时无法完成，请重试。';
}

export default function Simulation() {
  const { query, mutation } = useOpening();
  const [openingDate, setOpeningDate] = useState('');
  const [cash, setCash] = useState('');
  const [costIncludesFees, setCostIncludesFees] = useState('');
  const [rows, setRows] = useState<DraftHolding[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [inputError, setInputError] = useState('');
  const snapshot = query.data?.snapshot;

  function updateRow(id: string, field: keyof Omit<DraftHolding, 'id'>, value: string) {
    setRows((previous) => previous.map((row) => row.id === id ? { ...row, [field]: value } : row));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mutation.isPending) return;
    setInputError('');
    const invalid = rows.some((row) => !/^[1-9][0-9]*$/.test(row.quantity) || Number(row.quantity) > 1_000_000_000 || row.purchased_on > openingDate);
    if (invalid) { setInputError('持仓数量须为正整数，买入日期不能晚于起算日期。'); return; }
    if (!confirmed || !costIncludesFees) { setInputError('请确认成本口径与期初信息。'); return; }
    mutation.mutate({
      opening_date: openingDate,
      initial_cash: cash,
      cost_includes_fees: costIncludesFees === 'included',
      holdings: rows.map(({ id: _id, ...row }) => ({ ...row, quantity: Number(row.quantity) })),
    });
  }

  return (
    <main className="min-h-screen bg-page text-foreground px-5 py-10">
      <div className="mx-auto max-w-4xl space-y-7">
        <header className="space-y-3">
          <p className="text-sm text-muted-foreground">思迈挚融 · 模拟盘</p>
          <h1 className="text-2xl font-semibold">模拟投资账本</h1>
          <p className="text-sm text-muted-foreground">保存起始仓位和现金。期初余额不计为新成交，历史费用未知不补造。</p>
          <nav className="flex gap-5 text-sm underline" aria-label="产品入口"><Link to="/dashboard">研究工作台</Link><Link to="/setup/method">配置模型</Link></nav>
        </header>

        {query.isPending && <p role="status">正在读取账本…</p>}
        {query.isError && <div role="alert"><p>{errorMessage(query.error)}</p><Button className="mt-3" onClick={() => void query.refetch()}>重试读取</Button></div>}

        {snapshot && <section className="rounded-lg border border-border bg-card p-6 space-y-5" aria-label="期初快照">
          <h2 className="text-lg font-semibold">期初快照已保存 · 版本 {snapshot.version}</h2>
          <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-sm">
            <div><dt className="text-muted-foreground">账本起算日期</dt><dd>{snapshot.opening_date}</dd></div>
            <div><dt className="text-muted-foreground">期初现金</dt><dd className="tabular-nums">{snapshot.initial_cash} CNY</dd></div>
            <div><dt className="text-muted-foreground">原始成本口径</dt><dd>{snapshot.cost_includes_fees ? '含手续费' : '不含手续费'}；历史费用未知</dd></div>
          </dl>
          {snapshot.holdings.length === 0 ? <p>期初无持仓，仅保留现金。</p> : <div className="overflow-x-auto"><table className="w-full text-sm text-left">
            <caption className="sr-only">已保存的期初持仓与已知批次</caption>
            <thead><tr>{['证券', '数量', '精确总成本 CNY', '显示均价 CNY', '买入日期'].map((label) => <th className="p-2 border-b" key={label}>{label}</th>)}</tr></thead>
            <tbody>{snapshot.holdings.map((row, index) => <tr key={index}><td className="p-2">{row.name}<br />{row.code}.{row.exchange}</td><td className="p-2 tabular-nums">{row.quantity}</td><td className="p-2 tabular-nums">{row.total_cost}</td><td className="p-2 tabular-nums">{displayAverage(row.total_cost, row.quantity)}</td><td className="p-2">{row.acquisition_dates?.length ? row.acquisition_dates.join('、') : row.purchased_on}{row.batch_details_known === false && <p className="text-xs text-muted-foreground">批次数量与成本分配未知</p>}</td></tr>)}</tbody>
          </table></div>}
          <p className="text-sm text-muted-foreground">均价仅用于显示，按两位小数四舍五入；计算保留精确总成本。期初信息只读，当前数量、现金和后续动作见下方。</p>
          <p className="text-sm">已有期初值不能在此覆盖；后续更正将单独留痕。</p>
        </section>}

        {snapshot && <><SimulationReports /><SimulationLedger /></>}

        {query.isSuccess && !snapshot && <form onSubmit={submit} className="space-y-6">
          <fieldset disabled={mutation.isPending} className="space-y-6">
            <legend className="sr-only">初始化模拟账本</legend>
            <div className="grid sm:grid-cols-3 gap-4">
              <label className="text-sm space-y-2">账本起算日期<Input aria-label="账本起算日期" type="date" required value={openingDate} onChange={(e) => setOpeningDate(e.target.value)} /></label>
              <label className="text-sm space-y-2">初始现金（CNY）<Input aria-label="初始现金" inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,2})?" required value={cash} onChange={(e) => setCash(e.target.value)} /></label>
              <label className="text-sm space-y-2">原始成本是否含手续费<select className="h-9 w-full rounded-md border border-input bg-input px-2" required value={costIncludesFees} onChange={(e) => setCostIncludesFees(e.target.value)}><option value="">请选择已知口径</option><option value="excluded">不含手续费</option><option value="included">含手续费</option></select></label>
            </div>
            <div className="space-y-4">
              <div><h2 className="font-semibold">期初持仓批次</h2><p className="text-sm text-muted-foreground">按原始资料填写总成本，每批独立记录；无持仓时可以只初始化现金。</p></div>
              {rows.map((row, index) => <section className="rounded-lg border border-border bg-card p-4 space-y-3" key={row.id} aria-label={`持仓批次 ${index + 1}`}>
                <div className="flex items-center justify-between"><h3>批次 {index + 1}</h3><Button type="button" variant="ghost" size="sm" onClick={() => setRows((previous) => previous.filter((item) => item.id !== row.id))}>移除批次 {index + 1}</Button></div>
                <div className="grid sm:grid-cols-3 gap-3">
                  <label className="text-sm">六位证券代码<Input required pattern="[0-9]{6}" value={row.code} onChange={(e) => updateRow(row.id, 'code', e.target.value)} /></label>
                  <label className="text-sm">交易所<select className="h-9 w-full rounded-md border border-input bg-input px-2" value={row.exchange} onChange={(e) => updateRow(row.id, 'exchange', e.target.value)}><option value="SH">上海 SH</option><option value="SZ">深圳 SZ</option><option value="BJ">北京 BJ</option></select></label>
                  <label className="text-sm">证券名称<Input required maxLength={100} value={row.name} onChange={(e) => updateRow(row.id, 'name', e.target.value)} /></label>
                  <label className="text-sm">持有数量（股）<Input required inputMode="numeric" pattern="[1-9][0-9]*" value={row.quantity} onChange={(e) => updateRow(row.id, 'quantity', e.target.value)} /></label>
                  <label className="text-sm">原始总成本（CNY）<Input required inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,8})?" value={row.total_cost} onChange={(e) => updateRow(row.id, 'total_cost', e.target.value)} /></label>
                  <label className="text-sm">买入日期<Input required type="date" max={openingDate || undefined} value={row.purchased_on} onChange={(e) => updateRow(row.id, 'purchased_on', e.target.value)} /></label>
                </div>
              </section>)}
              <Button type="button" variant="outline" disabled={rows.length >= 100} onClick={() => setRows((previous) => [...previous, emptyHolding()])}>添加持仓批次</Button>
            </div>
            <label className="flex items-start gap-2 text-sm"><input type="checkbox" required checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} className="mt-1" />我已核对起算日期、持仓、成本和现金，本次仅保存模拟期初余额。</label>
            <Button type="submit">{mutation.isPending ? '正在保存…' : '确认初始化模拟账本'}</Button>
          </fieldset>
          {(inputError || mutation.isError) && <p role="alert">{inputError || errorMessage(mutation.error)}</p>}
        </form>}
      </div>
    </main>
  );
}
