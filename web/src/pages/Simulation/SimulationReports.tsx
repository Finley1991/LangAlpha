import { useState } from 'react';
import { useLocale } from '@/hooks/useLocale';
import { createDateFormatter } from '@/lib/format';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import type { ReportDetail } from './reportApi';
import { pendingReportRequest, finishReportRequest } from './pendingReport';
import { useReports } from './useReports';

const reportTime = createDateFormatter({ year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Shanghai' });
const financialTables = [
  ['income', '利润表'], ['balancesheet', '资产负债表'], ['cashflow', '现金流量表'],
  ['fina_indicator', '财务指标'],
] as const;
const financialMetricLabels: Record<string, string> = {
  basic_eps: '基本每股收益', total_revenue: '营业总收入', revenue: '营业收入',
  operate_profit: '营业利润', total_profit: '利润总额', n_income: '净利润',
  n_income_attr_p: '归母净利润', total_cur_assets: '流动资产合计', total_assets: '总资产',
  total_cur_liab: '流动负债合计', total_liab: '总负债',
  total_hldr_eqy_exc_min_int: '归母权益', money_cap: '货币资金',
  n_cashflow_act: '经营活动现金流净额', n_cashflow_inv_act: '投资活动现金流净额',
  n_cash_flows_fnc_act: '筹资活动现金流净额', c_cash_equ_end_period: '期末现金及现金等价物',
  eps: '每股收益', profit_dedt: '扣非净利润', gross_margin: '毛利率',
  current_ratio: '流动比率', quick_ratio: '速动比率', debt_to_assets: '资产负债率',
  roe: '净资产收益率', roa: '总资产收益率', ocfps: '每股经营活动现金流',
};

function message(error: unknown) {
  return error instanceof Error ? error.message : '暂时无法读取报告，请重试。';
}

function ReportBody({ data }: { data: ReportDetail }) {
  const locale = useLocale();
  const r = data.report;
  return <div className="space-y-4 text-sm break-words">
    {data.is_stale && <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-amber-950 dark:text-amber-100">此报告已失效：{data.expired ? '超过有效期' : data.current_version !== r.snapshot.version ? '账本版本已变化' : '北京时间检查日期已变化'}。历史事实仍保留；请重新生成检查。</p>}
    <section className="rounded-xl border border-border bg-muted/20 p-4 sm:p-5 space-y-3" aria-label="报告概览">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="inline-flex items-center rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-950 dark:text-amber-100">待核实</span>
        <span className="text-xs text-muted-foreground">账本版本 {r.snapshot.version}</span>
      </div>
      <p className="leading-6">{r.summary}</p>
      <dl className="grid gap-3 text-xs text-muted-foreground sm:grid-cols-2">
        <div><dt>数据截止（北京时间）</dt><dd className="mt-1 text-foreground">{reportTime(new Date(r.cutoff_at), locale)}</dd></div>
        <div><dt>报告失效（北京时间）</dt><dd className="mt-1 text-foreground">{reportTime(new Date(r.expires_at), locale)}</dd></div>
      </dl>
    </section>

    <section aria-label="日报账本事实" className="space-y-3">
      <h3 className="font-semibold">账本概览</h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ['现金', r.snapshot.cash], ['可用现金', r.snapshot.available_cash],
          ['预留现金', r.snapshot.reserved_cash], ['已实现盈亏', r.snapshot.realized_pnl],
        ].map(([label, value]) => <div key={label} className="rounded-lg border border-border bg-card p-3">
          <p className="text-xs text-muted-foreground">{label}</p><p className="mt-1 font-semibold tabular-nums">{value} <span className="text-xs font-normal text-muted-foreground">CNY</span></p>
        </div>)}
      </div>
      {r.opening_in_future && <p className="rounded-md bg-muted/40 p-3">起算日在未来，以下仅为预设快照。</p>}
      <p className="text-xs text-muted-foreground">{r.snapshot.sellable_basis}</p>
      {!r.checks.length && <p>此快照无持仓，只检查现金及缺失信息。</p>}
    </section>

    {!!r.checks.length && <section className="space-y-3" aria-label="持仓分析">
      <div><h3 className="font-semibold">持仓概览</h3><p className="mt-1 text-xs text-muted-foreground">先看实时盈亏与价格；展开单只持仓可回查依据和新闻。</p></div>
      <div className="space-y-3">{r.checks.map((check) => <article key={`${check.code}.${check.exchange}`} className="rounded-xl border border-border bg-card p-4 sm:p-5 space-y-4">
        <header className="flex flex-wrap items-start justify-between gap-2">
          <div><h4 className="font-semibold">{check.name}</h4><p className="mt-0.5 text-xs text-muted-foreground">{check.code}.{check.exchange}</p></div>
          <span className="rounded-full bg-muted px-2.5 py-1 text-xs">{check.judgment}</span>
        </header>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded-lg bg-muted/40 p-3">
            <p className="text-xs text-muted-foreground">新浪实时报价</p>
            {check.current_quote
              ? <><p className="mt-1 font-semibold tabular-nums">{check.current_quote.price} {check.current_quote.currency}</p><p className="mt-1 text-xs text-muted-foreground">{check.current_quote.freshness === 'fresh' ? '报价时间较新' : check.current_quote.freshness === 'future' ? '报价时间异常，待核实' : '报价已过期，待核实'}</p></>
              : <p className="mt-1 font-medium">本次未取得</p>}
          </div>
          <div className="rounded-lg border border-border p-3">
            <p className="text-xs text-muted-foreground">实时未实现盈亏</p>
            {check.realtime_pnl?.status === 'calculated' && check.realtime_pnl.unrealized_pnl != null && check.realtime_pnl.market_value != null
              ? <><p className={`mt-1 font-semibold tabular-nums ${check.realtime_pnl.unrealized_pnl.startsWith('-') ? 'text-emerald-700 dark:text-emerald-300' : 'text-red-600 dark:text-red-400'}`}>{check.realtime_pnl.unrealized_pnl.startsWith('-') ? '' : '+'}{check.realtime_pnl.unrealized_pnl} {check.realtime_pnl.currency}</p><p className="mt-1 text-xs text-muted-foreground">市值 {check.realtime_pnl.market_value} CNY</p></>
              : <><p className="mt-1 font-medium">待核实</p><p className="mt-1 text-xs text-muted-foreground">{check.realtime_pnl?.reason ?? '本次没有可用的最新报价'}</p></>}
          </div>
        </div>
        {check.realtime_pnl?.status === 'calculated' && check.realtime_pnl.as_of && <p className="text-xs text-muted-foreground">盈亏按实时报价 × 持仓数量 − 账本总成本计算；报价时间 {reportTime(new Date(check.realtime_pnl.as_of), locale)}（北京时间）。仅供本地参考，不是成交盈亏。</p>}
        <details className="group rounded-lg border border-border/70 px-3">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 py-3 font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
            <span>展开行情、公告、新闻与论点</span><span aria-hidden="true" className="text-muted-foreground transition group-open:rotate-180">⌄</span>
          </summary>
          <div className="space-y-4 border-t border-border/70 py-3">
            <div className="space-y-2"><h5 className="font-medium">持仓事实与判断依据</h5><p>事实：{check.fact}</p><p>判断：{check.judgment}</p><p>反证与不确定性：{check.counterargument}</p><p>重新检查条件：{check.trigger}</p></div>
            {check.current_quote && <div className="rounded-md bg-muted/40 p-3 space-y-1"><p className="font-medium">新浪实时行情快照：{check.current_quote.price} {check.current_quote.currency}</p><p>报价时间：{reportTime(new Date(check.current_quote.as_of), locale)}（北京时间）· 取数时间：{reportTime(new Date(check.current_quote.fetched_at), locale)}（北京时间）</p><p className="text-muted-foreground">非成交行情；没有核验停牌、交易日历或涨跌停边界。</p></div>}
            {check.closing_price && <div className="rounded-md bg-muted/40 p-3"><p className="font-medium">最新收盘价：{check.closing_price.close} CNY</p><p>{check.closing_price.trade_date} · 未复权历史收盘价，不用于当前成交。</p></div>}
            {check.financials && <details className="rounded-md border border-border/70 px-3"><summary className="cursor-pointer py-2 font-medium">财报数据（{Object.keys(check.financials.tables).length} 类结构化记录）</summary><div className="space-y-3 pb-3">
              {financialTables.map(([key, label]) => {
                const table = check.financials?.tables[key];
                if (!table) return null;
                return <section key={key} className="rounded-md bg-muted/40 p-3"><h5 className="font-medium">{label} · 报告期 {table.end_date}</h5><p className="mt-1 text-xs text-muted-foreground">公告日期：{table.ann_date ?? '未提供'} · 实际公告日期：{table.actual_ann_date ?? '未提供'}{table.report_type ? ` · 报表类型 ${table.report_type === '1' ? '1（合并报表）' : table.report_type}` : ''}</p><dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">{Object.entries(table.metrics).map(([field, value]) => <div key={field} className="flex justify-between gap-2"><dt className="text-muted-foreground">{financialMetricLabels[field] ?? field}</dt><dd className="break-all text-right tabular-nums">{value}</dd></div>)}</dl></section>;
              })}
              {check.financials.disclosure && <p className="rounded-md border border-border/70 p-3">披露计划 · 报告期 {check.financials.disclosure.end_date} · 计划披露日期：{check.financials.disclosure.planned_date ?? '未提供'} · 实际披露日期：{check.financials.disclosure.actual_date ?? '尚无已核实日期'}</p>}
              <p className="text-xs text-muted-foreground">结构化财务数值按接口原值展示，未做单位换算；仅供回查，不替代公告或年报正文、风险说明和审计意见，不据此形成买卖判断。取数时间：{reportTime(new Date(check.financials.fetched_at), locale)}（北京时间）。</p>
            </div></details>}
            {check.announcements?.length ? <details className="rounded-md border border-border/70 px-3"><summary className="cursor-pointer py-2 font-medium">近期公告（{check.announcements.length}）</summary><ul className="list-disc space-y-1 pb-3 pl-5">{check.announcements.map((item) => <li key={item.id ?? `${item.ann_date}-${item.title}`}>{item.ann_date} · {item.ann_type}{item.event_category && ` · ${item.event_category}`} · {item.title}{item.pdf_path && <> · <a className="underline" href={item.pdf_path} target="_blank" rel="noopener noreferrer">公告 PDF</a></>}</li>)}</ul></details> : null}
            {check.analyst_reports?.length ? <details className="rounded-md border border-border/70 px-3"><summary className="cursor-pointer py-2 font-medium">机构研报（{check.analyst_reports.length}，仅代表观点）</summary><ul className="list-disc space-y-1 pb-3 pl-5">{check.analyst_reports.map((item) => <li key={item.id ?? `${item.published_date}-${item.title}`}>{item.published_date} · {item.institution} · {item.rating}{item.target_price && ` · 目标价 ${item.target_price}`} · {item.title}{item.url && <> · <a className="underline" href={item.url} target="_blank" rel="noopener noreferrer">查看研报来源</a></>}</li>)}</ul></details> : null}
            {check.news?.length ? <details className="rounded-md border border-border/70 px-3"><summary className="cursor-pointer py-2 font-medium">个股新闻（{check.news.length}，尚未核实）</summary><ul className="list-disc space-y-2 pb-3 pl-5">{check.news.map((item) => <li key={item.id}>{item.published_at ? `${item.published_at} · ` : ''}{item.category === 'news' ? (item.publisher ?? '个股新闻') : '历史来源'} · <a className="underline" href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>{item.summary && <p className="mt-1 text-muted-foreground">{item.summary}</p>}</li>)}</ul><p className="pb-3 text-xs text-muted-foreground">新闻摘要仅作为回查线索；转载和摘要不代表公司正式披露或事实结论。</p></details> : null}
            <p className="text-xs text-muted-foreground">事实来源：{r.sources.filter((source) => check.source_ids.includes(source.source_id)).map((source) => source.title).join('；')}</p>
          </div>
        </details>
      </article>)}</div>
    </section>}

    <section aria-label="日报缺失数据" className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 space-y-2">
      <h3 className="font-semibold">仍需核实</h3>
      <ul className="list-disc pl-5 space-y-1 text-sm">{r.missing_data.map((item) => <li key={item}>{item}</li>)}</ul>
    </section>

    <details className="rounded-lg border border-border px-4">
      <summary className="cursor-pointer py-3 font-medium">来源、检查范围与报告校验</summary>
      <div className="space-y-4 border-t border-border py-3">
        <p className="text-muted-foreground">{r.validity}</p>
        <section aria-label="日报来源" className="space-y-3">
          <h3 className="font-semibold">来源与复查</h3>
          {r.sources.map((source) => <div key={source.source_id} className="rounded-md bg-muted/30 p-3">
            <p className="font-medium">{source.title}{source.snapshot_version != null && ` · 版本 ${source.snapshot_version}`}</p>
            <p className="mt-1 text-xs text-muted-foreground">数据对应时间：{reportTime(new Date(source.as_of), locale)}（北京时间）</p>
            {source.fetched_at && <p className="text-xs text-muted-foreground">取数时间：{reportTime(new Date(source.fetched_at), locale)}（北京时间；不代表供应商发布时间）</p>}
            {source.kind !== 'ledger_snapshot' && <a className="mt-1 inline-block underline" href={source.reference} target="_blank" rel="noopener noreferrer">查看数据源接口</a>}
            {source.evidence && <details className="mt-2"><summary className="cursor-pointer text-xs">查看冻结的来源参数和数据</summary><pre className="whitespace-pre-wrap break-all text-xs mt-2">{JSON.stringify(source.evidence, null, 2)}</pre></details>}
            <p className="mt-1 text-xs text-muted-foreground break-all">证据标识：{source.source_id}</p>
          </div>)}
        </section>
        <p className="text-xs text-muted-foreground">{r.scope}</p>
        <p className="break-all text-xs text-muted-foreground">{r.schema_version} · {r.report_id}<br />SHA-256：{data.content_hash}</p>
      </div>
    </details>
  </div>;
}

export default function SimulationReports({ active = true }: { active?: boolean }) {
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

  return <section id="simulation-reports" aria-label="持仓检查日报" className="rounded-xl border border-border bg-card p-4 sm:p-6 space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h2 className="text-lg font-semibold">持仓检查日报</h2><p className="mt-1 text-sm text-muted-foreground">冻结账本快照，查看行情盈亏与待核实证据。</p></div>
      <Button className="shrink-0" disabled={!current || generate.isPending || ledger.isFetching} onClick={create}>{generate.isPending ? '正在生成检查…' : '生成当前持仓检查'}</Button>
    </div>
    {ledger.isPending && <p role="status">正在读取当前账本…</p>}
    {ledger.isError && <div role="alert">{message(ledger.error)}<Button variant="outline" onClick={() => void ledger.refetch()}>重试读取账本</Button></div>}
    {inputError && <p role="alert">{inputError}</p>}
    {generate.isError && <p role="alert">{message(generate.error)}；可重试，同一请求不会重复保存。</p>}
    {history.isPending && current && <p role="status">正在读取报告历史…</p>}
    {history.isError && <div role="alert">{message(history.error)}<Button variant="outline" onClick={() => void history.refetch()}>重试读取报告历史</Button></div>}
    {history.isSuccess && !items.length && <p className="text-sm">尚无报告。点击生成后保存一份可回查的快照。</p>}
    <ul className="grid gap-2 sm:grid-cols-2">
      {items.map((item) => <li key={item.report_id} className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 rounded-lg border border-border p-3">
        <div className="text-sm"><p className="font-medium">{item.report_date} · {item.is_stale ? '历史快照已失效' : '对应当前账本'}</p><p className="mt-1 text-xs text-muted-foreground">生成于 {reportTime(new Date(item.generated_at), locale)}（北京时间）· 版本 {item.snapshot_version}</p></div>
        <Button variant="outline" size="sm" onClick={() => setSelectedId(item.report_id)}>查看报告 {item.sequence}</Button>
      </li>)}
    </ul>
    {history.hasNextPage && !history.isError && <Button variant="outline" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>{history.isFetchingNextPage ? '正在读取…' : '读取更早报告'}</Button>}
    <Dialog open={active && !!selectedId} onOpenChange={(open) => { if (!open) setSelectedId(undefined); }}>
      <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader><DialogTitle>{detail.data?.report.title ?? '持仓检查日报'}</DialogTitle><DialogDescription>仅模拟盘。数据按报告生成时点冻结；实时行情不代表成交价。</DialogDescription></DialogHeader>
        {detail.isPending && <p role="status">正在读取报告…</p>}
        {detail.isError && <div role="alert">{message(detail.error)}<Button variant="outline" onClick={() => void detail.refetch()}>重试读取报告</Button></div>}
        {detail.data && !detail.isError && <ReportBody data={detail.data} />}
      </DialogContent>
    </Dialog>
  </section>;
}
