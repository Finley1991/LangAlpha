import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useNavigate } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Simulation from '../Simulation';
import type { Ledger } from '../api';

const { opening, ledgerHook } = vi.hoisted(() => ({ opening: vi.fn(), ledgerHook: vi.fn() }));
vi.mock('../useOpening', () => ({ useOpening: opening }));
vi.mock('../useLedger', () => ({ useLedger: ledgerHook, useSimulationRecord: () => ({}) }));
vi.mock('../SimulationReports', () => ({ default: ({ active }: { active: boolean }) => <section aria-label="检查报告内容" data-active={active}>测试报告</section> }));
const mutation = () => ({ mutate: vi.fn(), reset: vi.fn(), isPending: false });
const fees = { version: 'sim-fees-v1', commission_rate: '0.0003', minimum_commission: '5.00', sell_tax_rate: '0.0005', transfer_rate: '0.00001', slippage_bps: '5', description: '虚构测试费用' };
let state: ReturnType<typeof setup>;
function setup() {
  const ledger: Ledger = { account_id: 'fictitious', version: 1, cash: '1000.00', available_cash: '1000.00', reserved_cash: '0.00', realized_pnl: '0.00', as_of: '2026-10-15T02:00:00Z', positions: [], suggestions: [], orders: [], fills: [], fees, sellable_basis: '待核实', history_limit: 100, market_data_status: 'unverified' };
  return { query: { data: { ledger }, refetch: vi.fn() }, events: { data: { pages: [{ events: [] }] } }, create: mutation(), confirm: mutation(), revise: mutation(), dismiss: mutation(), cancel: mutation() };
}
function HistoryControls() {
  const navigate = useNavigate();
  return <><button onClick={() => void navigate(-1)}>浏览器后退</button><button onClick={() => void navigate(1)}>浏览器前进</button></>;
}
function show(hash = '') {
  return render(<MemoryRouter initialEntries={[`/simulation${hash}`]}><Simulation /><HistoryControls /></MemoryRouter>);
}
beforeEach(() => {
  state = setup(); ledgerHook.mockImplementation(() => state);
  opening.mockReturnValue({ query: { isSuccess: true, data: { snapshot: { version: 1, opening_date: '2026-10-13', initial_cash: '1000.00', holdings: [], cost_includes_fees: false } } }, mutation: { isPending: false } });
});
const tab = (name: string) => screen.getByRole('tab', { name });

describe('simulation workspace', () => {
  it('shows only the selected area, with record types isolated from each other', () => {
    show();
    expect(tab('买卖')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('region', { name: '买卖操作区' })).toBeVisible();
    expect(screen.queryByRole('region', { name: '当前模拟账本' })).not.toBeInTheDocument();
    fireEvent.click(tab('持仓'));
    expect(screen.getByRole('region', { name: '当前模拟账本' })).toBeVisible();
    expect(screen.queryByRole('button', { name: '记录模拟买入' })).not.toBeInTheDocument();
    fireEvent.click(tab('记录'));
    expect(screen.getByRole('region', { name: '模拟订单' })).toBeVisible();
    expect(screen.queryByRole('region', { name: '模拟成交记录' })).not.toBeInTheDocument();
    for (const [name, region] of [['成交', '模拟成交记录'], ['历史建议', '历史建议'], ['操作审计', '模拟操作审计']]) {
      fireEvent.click(tab(name)); expect(screen.getByRole('region', { name: region })).toBeVisible();
      expect(screen.queryByRole('region', { name: '模拟订单' })).not.toBeInTheDocument();
    }
    fireEvent.click(tab('报告')); expect(screen.getByRole('region', { name: '检查报告内容' })).toBeVisible();
    expect(screen.getByRole('region', { name: '检查报告内容' })).toHaveAttribute('data-active', 'true');
    fireEvent.click(tab('期初')); expect(screen.getByRole('region', { name: '期初快照' })).toBeVisible();
    expect(screen.getByRole('tabpanel', { name: '期初' })).toHaveAttribute('tabindex', '0');
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('preserves draft fields and the retry identity across tab switches', () => {
    show(); fireEvent.click(screen.getByRole('button', { name: '记录模拟买入' }));
    const fields = { 建议证券代码: '600000', 建议证券名称: '虚构导航测试', 建议数量: '100', 建议限价: '1.00', 建议有效期: '2026-10-17T12:00', 建议理由: '测试', 反证与不确定性: '待核实' };
    for (const [label, value] of Object.entries(fields)) fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } });
    fireEvent.submit(screen.getByRole('button', { name: '保存待确认建议' }).closest('form')!);
    const id = state.create.mutate.mock.calls[0][0].client_request_id;
    fireEvent.click(tab('持仓')); fireEvent.click(tab('买卖'));
    expect(screen.getByLabelText('建议证券名称')).toHaveValue('虚构导航测试');
    expect(screen.getByLabelText('建议数量')).toHaveValue('100');
    fireEvent.submit(screen.getByRole('button', { name: '保存待确认建议' }).closest('form')!);
    expect(state.create.mutate.mock.calls[1][0].client_request_id).toBe(id);
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it.each([
    ['#simulation-trading', '买卖', '买卖操作区'], ['#simulation-reports', '报告', '检查报告内容'],
    ['#simulation-opening', '期初', '期初快照'], ['#simulation-orders', '记录', '模拟订单'],
    ['#simulation-history', '记录', '历史建议'], ['#unknown', '买卖', '买卖操作区'],
  ])('opens the right panel for %s', (hash, name, region) => {
    show(hash); expect(tab(name)).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('region', { name: region })).toBeVisible();
  });

  it('supports arrow/Home/End focus navigation and browser back/forward', () => {
    show(); tab('买卖').focus(); fireEvent.keyDown(tab('买卖'), { key: 'ArrowRight' });
    expect(tab('持仓')).toHaveFocus(); expect(tab('持仓')).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(tab('持仓'), { key: 'End' }); expect(tab('期初')).toHaveFocus();
    fireEvent.keyDown(tab('期初'), { key: 'Home' }); expect(tab('买卖')).toHaveFocus();
    fireEvent.click(tab('报告')); fireEvent.click(screen.getByRole('button', { name: '浏览器后退' }));
    expect(tab('买卖')).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('button', { name: '浏览器前进' }));
    expect(tab('报告')).toHaveAttribute('aria-selected', 'true');
    const panel = screen.getByRole('tabpanel', { name: '报告' });
    expect(tab('报告')).toHaveAttribute('aria-controls', panel.id);
    expect(within(screen.getByRole('tablist', { name: '模拟盘工作区' })).getAllByRole('tab').filter((t) => t.tabIndex === 0)).toHaveLength(1);
  });
});
