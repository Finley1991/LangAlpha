import { fireEvent, render, screen, within, act } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SimulationReports from '../SimulationReports';
import type { ReportDetail, ReportPage, ReportRequest } from '../reportApi';

const { useReportsMock } = vi.hoisted(() => ({ useReportsMock: vi.fn() }));
vi.mock('../useReports', () => ({ useReports: useReportsMock }));
const report: ReportDetail = {
  sequence: 1, content_hash: 'a'.repeat(64), current_version: 2, is_stale: true, expired: false,
  report: {
    schema_version: 'simulation-report-v1', report_id: 'older-report', report_date: '2026-10-14',
    generated_at: '2026-10-14T02:00:00Z', cutoff_at: '2026-10-14T02:00:00Z', expires_at: '2026-10-15T02:00:00Z',
    status: 'awaiting_data', title: '持仓检查日报 · 数据待核实', summary: '市场事实和论点不足。',
    validity: '账本变化后须重新检查。', opening_in_future: false,
    snapshot: { account_id: 'account', version: 1, as_of: '2026-10-14T02:00:00Z', cash: '1000.00', available_cash: '1000.00', reserved_cash: '0.00', realized_pnl: '0.00000000', positions: [], sellable_basis: '按已知日期保守计算' },
    sources: [{ source_id: 'source-1', kind: 'ledger_snapshot', title: '冻结账本证据', reference: '/reports/older-report#snapshot', as_of: '2026-10-14T02:00:00Z', snapshot_version: 1 }],
    checks: [{ code: '000001', exchange: 'SZ', name: '虚构报告持仓', fact: '精确成本 1234.56780001 CNY', source_ids: ['source-1'], judgment: '待核实', counterargument: '缺少市场事实', trigger: '补齐证据后重新检查', missing_data: ['行情未核验'] }],
    missing_data: ['行情未核验', '持仓论点未建立'], scope: '仅账本检查；不创建订单。',
  },
};
function setup() {
  return {
    ledger: { data: { ledger: { account_id: 'account-a', version: 2 } }, isPending: false, isError: false, isFetching: false, refetch: vi.fn() },
    history: { data: { pages: [{ items: [{ report_id: 'older-report', sequence: 1, report_date: '2026-10-14', generated_at: '2026-10-14T02:00:00Z', expires_at: '2026-10-15T02:00:00Z', snapshot_version: 1, title: report.report.title, status: 'awaiting_data', is_stale: true, expired: false }], next_cursor: 1 }] as ReportPage[] }, isPending: false, isSuccess: true, isError: false, isFetchingNextPage: false, hasNextPage: true, fetchNextPage: vi.fn(), refetch: vi.fn() },
    detail: { data: report, isPending: false, isError: false, error: null as Error | null, refetch: vi.fn() },
    generate: { mutate: vi.fn(), isPending: false, isError: false, error: null as Error | null },
  };
}
function openEvidence(dialog: HTMLElement) {
  fireEvent.click(within(dialog).getByText('展开行情、公告、新闻与论点'));
}
function openSources(dialog: HTMLElement) {
  fireEvent.click(within(dialog).getByText('来源、检查范围与报告校验'));
}
let state: ReturnType<typeof setup>;
beforeEach(() => { sessionStorage.clear(); state = setup(); useReportsMock.mockImplementation(() => state); });

describe('frozen checking reports', () => {
  it('keeps the position summary scannable and collapses secondary evidence', () => {
    state.detail.data = structuredClone(report);
    Object.assign(state.detail.data.report.checks[0], {
      realtime_pnl: {
        status: 'calculated', market_value: '2852.50', unrealized_pnl: '1617.93',
        currency: 'CNY', as_of: '2026-10-16T01:59:40Z', reason: null, source_ids: ['quote-1'],
      },
      news: [{ id: 'news-1', category: 'news', title: '虚构新闻标题', url: 'https://example.invalid/news', source_ids: ['news-source'] }],
    });
    render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/实时未实现盈亏/)).toBeInTheDocument();
    const evidence = within(dialog).getByText('展开行情、公告、新闻与论点').closest('details');
    expect(evidence).not.toHaveAttribute('open');
    openEvidence(dialog);
    expect(evidence).toHaveAttribute('open');
    expect(within(dialog).getByRole('link', { name: '虚构新闻标题' })).toBeInTheDocument();
  });

  it('shows frozen closing evidence with retrieval time and source parameters', () => {
    state.detail.data = structuredClone(report);
    state.detail.data.report.sources.push({
      source_id: 'closing-1', kind: 'closing_price', title: 'Tushare 未复权日线收盘价',
      reference: 'https://tushare.pro/document/2?doc_id=27', as_of: '2026-10-13T07:00:00Z',
      fetched_at: '2026-10-14T02:00:00Z',
      evidence: { api: 'daily', params: { ts_code: '000001.SZ', trade_date: '20261013' }, rows: [{ ts_code: '000001.SZ', close: '10.23' }] },
    });
    render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    const dialog = screen.getByRole('dialog');
    openSources(dialog);
    fireEvent.click(within(dialog).getByText('查看冻结的来源参数和数据'));
    const sources = within(dialog).getByRole('region', { name: '日报来源' });
    expect(within(sources).getByText(/不代表供应商发布时间/)).toBeInTheDocument();
    expect(within(sources).getByRole('link', { name: '查看数据源接口' })).toHaveAttribute('href', 'https://tushare.pro/document/2?doc_id=27');
    expect(within(sources).getByText(/"trade_date": "20261013"/)).toBeInTheDocument();
    expect(within(sources).queryByText(/版本 undefined/)).not.toBeInTheDocument();
  });

  it('shows announcement metadata and labels analyst ratings as opinions', () => {
    state.detail.data = structuredClone(report);
    state.detail.data.report.checks[0].announcements = [{
      id: 'announcement-1', paper_code: '000001', ann_date: '2026-04-01',
      ann_type: '年度报告', event_category: '财务指标', title: '虚构年报',
      pdf_path: 'https://example.invalid/fictional.pdf',
    }];
    state.detail.data.report.checks[0].analyst_reports = [{
      id: 'research-1', symbol: 'sz000001', published_date: '2026-04-02',
      institution: '虚构机构', rating: '中性', title: '虚构评级报告',
    }];
    render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    const dialog = screen.getByRole('dialog');
    openEvidence(dialog);
    expect(within(dialog).getByText(/虚构年报/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByText('近期公告（1）'));
    expect(within(dialog).getByRole('link', { name: '公告 PDF' })).toHaveAttribute('href', 'https://example.invalid/fictional.pdf');
    fireEvent.click(within(dialog).getByText(/机构研报（1，仅代表观点）/));
    expect(within(dialog).getByText(/虚构评级报告/)).toBeInTheDocument();
  });

  it('shows attributed stock news as unverified source material', () => {
    state.detail.data = structuredClone(report);
    state.detail.data.report.checks[0].news = [{
      id: 'news-1', category: 'news', title: '虚构新闻标题', summary: '这是待核实的新闻摘要。',
      publisher: '新浪财经', url: 'https://finance.sina.com.cn/news/example.shtml', source_ids: ['news-source'],
    }];
    state.detail.data.report.sources.push({
      source_id: 'news-source', kind: 'stock_news', title: '新浪财经个股新闻',
      reference: 'https://finance.sina.com.cn', as_of: '2026-10-14T02:00:00Z',
      evidence: { provider: 'sina' },
    });
    render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    const dialog = screen.getByRole('dialog');
    openEvidence(dialog);
    fireEvent.click(within(dialog).getByText(/个股新闻（1，尚未核实）/));
    expect(within(dialog).getByRole('link', { name: '虚构新闻标题' })).toHaveAttribute('href', 'https://finance.sina.com.cn/news/example.shtml');
    expect(within(dialog).getByText(/新闻摘要仅作为回查线索/)).toBeInTheDocument();
  });

  it('shows a real-time quote snapshot separately from closing prices', () => {
    state.detail.data = structuredClone(report);
    Object.assign(state.detail.data.report.checks[0], {
      current_quote: {
        price: '11.41', currency: 'CNY', as_of: '2026-10-16T01:59:40Z',
        fetched_at: '2026-10-16T02:00:00Z', freshness: 'fresh',
        freshness_as_of: '2026-10-16T02:00:00Z', source_ids: ['quote-1'],
      },
      realtime_pnl: {
        status: 'calculated', market_value: '2852.50', unrealized_pnl: '1617.93',
        currency: 'CNY', as_of: '2026-10-16T01:59:40Z', reason: null,
        source_ids: ['quote-1'],
      },
    });
    state.detail.data.report.sources.push({
      source_id: 'quote-1', kind: 'live_quote', title: '新浪实时行情快照',
      reference: 'https://base.finance.sina.cn/stock/getHqInfo',
      as_of: '2026-10-16T01:59:40Z', fetched_at: '2026-10-16T02:00:00Z',
      evidence: { api: 'getHqInfo', params: { symbol: 'sz000001' }, rows: [] },
    });
    render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent(/11\.41 CNY/);
    expect(dialog).toHaveTextContent(/实时未实现盈亏/);
    expect(dialog).toHaveTextContent(/1617\.93 CNY/);
    expect(dialog).toHaveTextContent(/市值 2852\.50 CNY/);
    expect(within(dialog).getByText(/\+1617\.93 CNY/)).toHaveClass('text-red-600');
    openEvidence(dialog);
    expect(dialog).toHaveTextContent(/报价时间/);
    expect(dialog).toHaveTextContent(/非成交行情/);
  });

  it('marks real-time P&L pending when the quote is stale', () => {
    state.detail.data = structuredClone(report);
    state.detail.data.report.checks[0].realtime_pnl = {
      status: 'pending_verification', market_value: null, unrealized_pnl: null,
      currency: 'CNY', as_of: null, reason: '实时行情已过期，盈亏待核实',
      source_ids: ['quote-1'],
    };
    render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    expect(screen.getByRole('dialog')).toHaveTextContent(/实时未实现盈亏/);
    expect(screen.getByRole('dialog')).toHaveTextContent(/实时行情已过期/);
  });

  it('shows storage failure without posting a report that cannot be retried safely', () => {
    render(<SimulationReports />);
    const blocked = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError'); });
    try {
      fireEvent.click(screen.getByRole('button', { name: '生成当前持仓检查' }));
      expect(screen.getByRole('alert')).toHaveTextContent('无法保存重试标识');
      expect(state.generate.mutate).not.toHaveBeenCalled();
    } finally { blocked.mockRestore(); }
  });

  it('restores a lost-response request after remount and isolates accounts', () => {
    const first = render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '生成当前持仓检查' }));
    const submitted = state.generate.mutate.mock.calls[0][0];
    first.unmount();
    const second = render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '生成当前持仓检查' }));
    expect(state.generate.mutate.mock.calls[1][0]).toEqual(submitted);
    second.unmount();
    state.ledger = { ...state.ledger, data: { ledger: { account_id: 'account-b', version: 2 } } };
    render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '生成当前持仓检查' }));
    expect(state.generate.mutate.mock.calls[2][0].client_request_id).not.toBe(submitted.client_request_id);
  });

  it('stops showing cached history when verification fails', () => {
    const { rerender } = render(<SimulationReports />);
    expect(screen.getByRole('button', { name: '查看报告 1' })).toBeInTheDocument();
    state.history = { ...state.history, isError: true };
    rerender(<SimulationReports />);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看报告 1' })).not.toBeInTheDocument();
  });

  it('generates only on click, binds the current version and keeps failed retry identity', () => {
    const { rerender } = render(<SimulationReports />);
    expect(state.generate.mutate).not.toHaveBeenCalled();
    const action = screen.getByRole('button', { name: '生成当前持仓检查' });
    fireEvent.click(action);
    const req = state.generate.mutate.mock.calls[0][0] as ReportRequest;
    expect(req.account_version).toBe(2);
    expect(Object.keys(req).sort()).toEqual(['account_version', 'client_request_id']);
    state.generate = { ...state.generate, isError: true, error: new Error('连接中断') };
    rerender(<SimulationReports />);
    expect(screen.getByRole('alert')).toHaveTextContent('连接中断');
    fireEvent.click(action);
    expect(state.generate.mutate.mock.calls[1][0]).toEqual(req);
    state.ledger = { ...state.ledger, data: { ledger: { account_id: 'account-a', version: 3 } } };
    rerender(<SimulationReports />); fireEvent.click(action);
    expect(state.generate.mutate.mock.calls[2][0].client_request_id).not.toBe(req.client_request_id);
    expect(state.generate.mutate.mock.calls[2][0].account_version).toBe(3);
  });

  it('blocks duplicate pending submission and opens the persisted report on success', () => {
    const { rerender } = render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '生成当前持仓检查' }));
    const onSuccess = state.generate.mutate.mock.calls[0][1].onSuccess;
    state.generate = { ...state.generate, isPending: true }; rerender(<SimulationReports />);
    expect(screen.getByRole('button', { name: '正在生成检查…' })).toBeDisabled();
    act(() => onSuccess(report));
    expect(useReportsMock).toHaveBeenLastCalledWith('older-report');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('reads older evidence without replacing frozen amounts with the current ledger', () => {
    render(<SimulationReports />);
    fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    const dialog = screen.getByRole('dialog');
    openEvidence(dialog);
    expect(within(dialog).getByRole('status')).toHaveTextContent('账本版本已变化');
    expect(within(dialog).getAllByText(/1000\.00/).length).toBeGreaterThan(0);
    expect(within(dialog).getByText(/1234.56780001/)).toBeInTheDocument();
    expect(within(dialog).getByText(/反证与不确定性：缺少市场事实/)).toBeInTheDocument();
    expect(within(dialog).getByText(/重新检查条件：补齐证据/)).toBeInTheDocument();
    expect(within(dialog).getByRole('region', { name: '日报缺失数据' })).toHaveTextContent('行情未核验');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    fireEvent.click(screen.getByRole('button', { name: '读取更早报告' }));
    expect(state.history.fetchNextPage).toHaveBeenCalledOnce();
  });

  it('hides cached body when integrity validation fails and offers an explicit retry', () => {
    state.detail = { ...state.detail, isError: true, error: new Error('报告数据口径异常') };
    render(<SimulationReports />); fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('alert')).toHaveTextContent('报告数据口径异常');
    expect(within(dialog).queryByText(/现金 1000.00/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: '重试读取报告' }));
    expect(state.detail.refetch).toHaveBeenCalledOnce();
  });

  it('labels future openings and time expiry independently of market readiness', () => {
    state.detail = { ...state.detail, data: { ...report, expired: true, report: { ...report.report, opening_in_future: true } } };
    render(<SimulationReports />); fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    expect(screen.getByText(/起算日在未来/)).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByRole('status')).toHaveTextContent('超过有效期');
    expect(screen.getByText(/报告失效（北京时间）/)).toBeInTheDocument();
  });
  it('does not open a portal when generation finishes in another tab', () => {
    const { rerender } = render(<SimulationReports active />);
    fireEvent.click(screen.getByRole('button', { name: '生成当前持仓检查' }));
    const onSuccess = state.generate.mutate.mock.calls[0][1].onSuccess;
    rerender(<SimulationReports active={false} />);
    act(() => onSuccess(report));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    rerender(<SimulationReports active />);
    expect(screen.getByRole('dialog')).toBeVisible();
    expect(useReportsMock).toHaveBeenLastCalledWith('older-report');
  });

  it('hides an open report when navigation leaves its tab and restores the selection on return', () => {
    const { rerender } = render(<SimulationReports active />);
    fireEvent.click(screen.getByRole('button', { name: '查看报告 1' }));
    expect(screen.getByRole('dialog')).toBeVisible();
    rerender(<SimulationReports active={false} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    rerender(<SimulationReports active />);
    expect(screen.getByRole('dialog')).toBeVisible();
  });

});
