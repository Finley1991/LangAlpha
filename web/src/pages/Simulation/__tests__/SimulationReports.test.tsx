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
let state: ReturnType<typeof setup>;
beforeEach(() => { sessionStorage.clear(); state = setup(); useReportsMock.mockImplementation(() => state); });

describe('frozen checking reports', () => {
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
    expect(within(dialog).getByRole('status')).toHaveTextContent('账本版本已变化');
    expect(within(dialog).getByText(/现金 1000.00/)).toBeInTheDocument();
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
    expect(screen.getByText(/失效时间（北京时间）/)).toBeInTheDocument();
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
