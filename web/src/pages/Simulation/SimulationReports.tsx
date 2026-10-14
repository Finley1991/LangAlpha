import { useState } from 'react';
import { useLocale } from '@/hooks/useLocale';
import { createDateFormatter } from '@/lib/format';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { ReportDetail } from './reportApi';
import { pendingReportRequest, finishReportRequest } from './pendingReport';
import { useReports } from './useReports';

const reportTime = createDateFormatter({ year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Shanghai' });

function message(error: unknown) {
  return error instanceof Error ? error.message : '暂时无法读取报告，请重试。';
}

function ReportBody({ data }: { data: ReportDetail }) {
  const locale = useLocale();
  const r = data.report;
  return <div className="space-y-5 text-sm break-words">
    {data.is_stale && <p role="status" className="rounded-md border border-border p-3">此报告已失效：{data.expired ? '超过有效期' : data.current_version !== r.snapshot.version ? '账本版本已变化' : '北京时间检查日期已变化'}。历史事实仍保留；请重新生成检查。</p>}
    <p>{r.summary}</p>
    <dl className="grid sm:grid-cols-2 gap-3">
      <div><dt className="text-muted-foreground">数据截止时间（北京时间）</dt><dd>{reportTime(new Date(r.cutoff_at), locale)}</dd></div>
      <div><dt className="text-muted-foreground">失效时间（北京时间）</dt><dd>{reportTime(new Date(r.expires_at), locale)}</dd></div>
      <div><dt className="text-muted-foreground">当时账本版本</dt><dd>{r.snapshot.version}</dd></div>
      <div><dt className="text-muted-foreground">检查状态</dt><dd>待核实</dd></div>
    </dl>
    <p>{r.validity}</p>
    <section aria-label="日报账本事实" className="rounded-md border border-border p-4 space-y-2">
      <h3 className="font-semibold">冻结的账本事实</h3>
      {r.opening_in_future && <p>起算日在未来，以下仅为预设快照。</p>}
      <p className="tabular-nums">现金 {r.snapshot.cash} CNY · 可用 {r.snapshot.available_cash} CNY · 预留 {r.snapshot.reserved_cash} CNY</p>
      <p className="tabular-nums">起算后已实现盈亏 {r.snapshot.realized_pnl} CNY</p>
      <p className="text-muted-foreground">{r.snapshot.sellable_basis}</p>
      {!r.checks.length && <p>此快照无持仓，只检查现金及缺失信息。</p>}
    </section>
    {r.checks.map((check) => <section key={`${check.code}.${check.exchange}`} className="rounded-md border border-border p-4 space-y-2">
      <h3 className="font-semibold">{check.name} · {check.code}.{check.exchange}</h3>
      <p>事实：{check.fact}</p><p>判断：{check.judgment}</p>
      <p>反证与不确定性：{check.counterargument}</p><p>重新检查条件：{check.trigger}</p>
      <p className="text-muted-foreground">事实来源：{r.sources.filter((source) => check.source_ids.includes(source.source_id)).map((source) => source.title).join('；')}</p>
    </section>)}
    <section aria-label="日报缺失数据" className="space-y-2">
      <h3 className="font-semibold">仍需核实</h3>
      <ul className="list-disc pl-5 space-y-1">{r.missing_data.map((item) => <li key={item}>{item}</li>)}</ul>
    </section>
    <section aria-label="日报来源" className="space-y-2">
      <h3 className="font-semibold">来源与复查</h3>
      {r.sources.map((source) => <div key={source.source_id}>
        <p>{source.title} · 版本 {source.snapshot_version}</p>
        <p>记录时间：{reportTime(new Date(source.as_of), locale)}（北京时间）</p>
        <p className="text-xs text-muted-foreground break-all">证据标识：{source.source_id}</p>
      </div>)}
      <details><summary className="cursor-pointer">报告校验信息</summary><p className="break-all text-xs mt-2">{r.schema_version} · {r.report_id}<br />SHA-256：{data.content_hash}</p></details>
    </section>
    <p className="text-muted-foreground">{r.scope}</p>
  </div>;
}

export default function SimulationReports() {
  const locale = useLocale();
  const [selectedId, setSelectedId] = useState<string>();
  const [inputError, setInputError] = useState('');
  const { ledger, history, detail, generate } = useReports(selectedId);
  const current = ledger.data?.ledger;
  const items = history.isError ? [] : history.data?.pages.flatMap((page) => page.items) ?? [];

  function create() {
    if (!current || generate.isPending) return;
    setInputError('');
    try {
      const next = pendingReportRequest(current.account_id, current.version);
      generate.mutate(next, { onSuccess: (data) => {
        finishReportRequest(current.account_id, next);
        setSelectedId(data.report.report_id);
      } });
    } catch (error) { setInputError(message(error)); }
  }

  return <section id="simulation-reports" aria-label="持仓检查日报" className="rounded-lg border border-border bg-card p-6 space-y-4">
    <h2 className="text-lg font-semibold">持仓检查日报</h2>
    <p className="text-sm text-muted-foreground">保存当前事实、来源、缺失信息与重新检查条件。行情和公告尚未接通，报告会明确标记待核实；生成或阅读报告不会创建订单。</p>
    {ledger.isPending && <p role="status">正在读取当前账本…</p>}
    {ledger.isError && <div role="alert">{message(ledger.error)}<Button variant="outline" onClick={() => void ledger.refetch()}>重试读取账本</Button></div>}
    <Button disabled={!current || generate.isPending || ledger.isFetching} onClick={create}>{generate.isPending ? '正在生成检查…' : '生成当前持仓检查'}</Button>
    {inputError && <p role="alert">{inputError}</p>}
    {generate.isError && <p role="alert">{message(generate.error)}；可重试，同一请求不会重复保存。</p>}
    {history.isPending && current && <p role="status">正在读取报告历史…</p>}
    {history.isError && <div role="alert">{message(history.error)}<Button variant="outline" onClick={() => void history.refetch()}>重试读取报告历史</Button></div>}
    {history.isSuccess && !items.length && <p className="text-sm">尚无报告。点击生成后保存一份可回查的快照。</p>}
    <ul className="space-y-3">
      {items.map((item) => <li key={item.report_id} className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 border-t border-border pt-3">
        <div className="text-sm"><p>{item.report_date} · 数据待核实 · {item.is_stale ? '历史快照已失效' : '对应当前账本'}</p><p className="text-muted-foreground">生成于 {reportTime(new Date(item.generated_at), locale)}（北京时间）· 版本 {item.snapshot_version}</p></div>
        <Button variant="outline" onClick={() => setSelectedId(item.report_id)}>查看报告 {item.sequence}</Button>
      </li>)}
    </ul>
    {history.hasNextPage && !history.isError && <Button variant="outline" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>{history.isFetchingNextPage ? '正在读取…' : '读取更早报告'}</Button>}
    <Dialog open={!!selectedId} onOpenChange={(open) => { if (!open) setSelectedId(undefined); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader><DialogTitle>{detail.data?.report.title ?? '持仓检查日报'}</DialogTitle><DialogDescription>仅模拟盘。这里展示生成时的冻结事实，缺失数据保留待核实。</DialogDescription></DialogHeader>
        {detail.isPending && <p role="status">正在读取报告…</p>}
        {detail.isError && <div role="alert">{message(detail.error)}<Button variant="outline" onClick={() => void detail.refetch()}>重试读取报告</Button></div>}
        {detail.data && !detail.isError && <ReportBody data={detail.data} />}
      </DialogContent>
    </Dialog>
  </section>;
}
