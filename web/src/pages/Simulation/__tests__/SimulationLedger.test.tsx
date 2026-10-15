import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SimulationLedger from '../SimulationLedger';
import type { EventPage, Ledger, Suggestion } from '../api';

const { useLedgerMock, useRecordMock } = vi.hoisted(() => ({ useLedgerMock: vi.fn(), useRecordMock: vi.fn() }));
vi.mock('../useLedger', () => ({ useLedger: useLedgerMock, useSimulationRecord: useRecordMock }));

const fees = { version: 'sim-fees-v1', commission_rate: '0.0003', minimum_commission: '5.00', sell_tax_rate: '0.0005', transfer_rate: '0.00001', slippage_bps: '5', description: '实验参数，非券商费率。' };
const card: Suggestion = {
  client_request_id: 'draft-id', suggestion_id: 'suggestion-id', account_version: 1, suggestion_version: 1,
  code: '600000', exchange: 'SH', name: '虚构测试证券', side: 'buy', quantity: 100, limit_price: '1.00',
  reason: '虚构理由', counterargument: '数据未知', expires_at: '2026-10-14T07:00:00Z', created_at: '2026-10-14T02:00:00Z',
  status: 'pending', fees, estimated_cash_delta: '-105.00', estimated_cash_after: '895.00', estimated_fees: { total: '5.00' }, reference_status: 'unverified',
};
const ledger: Ledger = { account_id: 'account-id', version: 1, as_of: '2026-10-14T02:00:00Z', cash: '1000.00', available_cash: '1000.00', reserved_cash: '0.00', realized_pnl: '0.00000000', positions: [], suggestions: [card], orders: [], fills: [], fees, sellable_basis: '成交仍须验证日历', history_limit: 100, market_data_status: 'unverified' };

function mutation() { return { mutate: vi.fn(), reset: vi.fn(), isPending: false, isError: false, error: null as Error | null }; }
let state: ReturnType<typeof setup>;
function setup() {
  return { query: { data: { ledger: structuredClone(ledger) }, isPending: false, isError: false, isFetching: false, refetch: vi.fn() },
    events: { data: { pages: [{ events: [], next_cursor: null }] as EventPage[] }, hasNextPage: false, isPending: false, isFetchingNextPage: false, fetchNextPage: vi.fn() },
    create: mutation(), confirm: mutation(), revise: mutation(), dismiss: mutation(), cancel: mutation() };
}
beforeEach(() => { state = setup(); useLedgerMock.mockImplementation(() => state); useRecordMock.mockReturnValue({}); });

describe('explicit simulation confirmation', () => {
  it('opening the dialog creates no order; acknowledgement is required for the exact card version', () => {
    render(<SimulationLedger />);
    fireEvent.click(screen.getByRole('button', { name: '核对并确认模拟买入' }));
    const dialog = screen.getByRole('dialog');
    const action = within(dialog).getByRole('button', { name: '确认模拟买入' });
    expect(action).toBeDisabled();
    expect(state.confirm.mutate).not.toHaveBeenCalled();
    expect(within(dialog).getByText(/虚构测试证券/)).toBeInTheDocument();
    expect(within(dialog).getByText(/限价估算费用 5.00/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(action);
    expect(state.confirm.mutate).toHaveBeenCalledWith({ suggestion_id: 'suggestion-id', suggestion_version: 1, account_version: 1, acknowledge_simulation: true }, expect.any(Object));
  });

  it('blocks stale cards and a pending confirmation from repeated clicks', () => {
    const { rerender } = render(<SimulationLedger />);
    fireEvent.click(screen.getByRole('button', { name: '核对并确认模拟买入' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('checkbox'));
    state = { ...state, confirm: { ...state.confirm, isPending: true } };
    rerender(<SimulationLedger />);
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: '正在确认…' })).toBeDisabled();
    state = { ...state, confirm: { ...state.confirm, isPending: false }, query: { ...state.query, data: { ledger: { ...state.query.data.ledger, version: 2 } } } };
    rerender(<SimulationLedger />);
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: '确认模拟买入' })).toBeDisabled();
    expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent('账本或预留已变化');
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('saving a draft never confirms and retry keeps the same request identity', () => {
    render(<SimulationLedger />);
    fireEvent.click(screen.getByRole('button', { name: '记录模拟买入' }));
    const fields = { 建议证券代码: '600001', 建议证券名称: '虚构草稿', 建议数量: '100', 建议限价: '1.00', 建议有效期: '2026-10-14T15:00', 建议理由: '测试', 反证与不确定性: '未知' };
    for (const [label, value] of Object.entries(fields)) fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } });
    const save = screen.getByRole('button', { name: '保存待确认建议' });
    fireEvent.submit(save.closest('form')!); fireEvent.submit(save.closest('form')!);
    const first = state.create.mutate.mock.calls[0][0];
    expect(first.limit_price).toBe('1.00'); expect(first.quantity).toBe(100);
    expect(state.create.mutate.mock.calls[1][0].client_request_id).toBe(first.client_request_id);
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('pending orders wait for verified quotes and can be cancelled without another confirmation', () => {
    state.query.data.ledger.orders = [{ ...card, order_id: 'order-id', status: 'pending', confirmed_at: '2026-10-14T02:01:00Z', reserved_cash: '105.00', execution_status: 'awaiting_verified_quote' }];
    render(<SimulationLedger section="orders" />);
    expect(screen.getByText(/当前不会成交/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消模拟订单' }));
    expect(state.cancel.mutate).toHaveBeenCalledWith('order-id');
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('keeps failed confirmation visible with no automatic retry', () => {
    const { rerender } = render(<SimulationLedger />);
    fireEvent.click(screen.getByRole('button', { name: '核对并确认模拟买入' }));
    state = { ...state, confirm: { ...state.confirm, error: new Error('可用现金不足'), isError: true } };
    rerender(<SimulationLedger />);
    expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent('可用现金不足');
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('can page and read an older audit record outside the current suggestion list', () => {
    state.events.hasNextPage = true;
    state.events.data.pages[0] = { events: [{ event_id: 1, event_type: 'suggested', cash_delta: '0.00', created_at: card.created_at, payload: { suggestion_id: 'older-card' } }], next_cursor: 1 };
    useRecordMock.mockImplementation((_kind: string, id: string) => id ? { data: { result: { ...card, suggestion_id: id } } } : {});
    render(<SimulationLedger section="audit" />);
    fireEvent.click(screen.getByRole('button', { name: '读取更早审计' }));
    expect(state.events.fetchNextPage).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '查看建议' }));
    expect(useRecordMock).toHaveBeenLastCalledWith('suggestions', 'older-card');
    expect(within(screen.getByRole('dialog')).getByText(/记录 older-card/)).toBeInTheDocument();
    expect(within(screen.getByRole('dialog')).getByText('理由：虚构理由')).toBeInTheDocument();
  });
  it('exposes buy and sell entry points and separates pending cards from history', () => {
    state.query.data.ledger.suggestions.push({ ...card, suggestion_id: 'closed', status: 'cancelled' });
    render(<SimulationLedger />);
    expect(screen.getByRole('region', { name: '买卖操作区' })).toHaveTextContent('1 笔待确认');
    expect(screen.getByText(/AI 自动分析转建议卡尚未接通/)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '历史建议' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: '记录手动建议' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '记录模拟卖出' }));
    expect(screen.getByLabelText('建议方向')).toHaveValue('sell');
    fireEvent.click(screen.getByRole('button', { name: '记录模拟买入' }));
    expect(screen.getByLabelText('建议方向')).toHaveValue('buy');
    expect(state.create.mutate).not.toHaveBeenCalled();
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('editing quantity revokes acknowledgement and requires a server recalculation before confirmation', () => {
    render(<SimulationLedger />);
    fireEvent.click(screen.getByRole('button', { name: '修改数量 / 限价' }));
    const dialog = screen.getByRole('dialog');
    const check = within(dialog).getByRole('checkbox');
    fireEvent.click(check);
    fireEvent.change(within(dialog).getByLabelText('本次数量', { exact: true }), { target: { value: '300' } });
    expect(check).not.toBeChecked();
    expect(check).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: '确认模拟买入' })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('button', { name: '重新计算并保存建议' }));
    const first = state.revise.mutate.mock.calls[0][0];
    expect(first).toMatchObject({ suggestion_id: card.suggestion_id, quantity: 300, limit_price: '1.00', suggestion_version: 1, account_version: 1 });
    fireEvent.click(within(dialog).getByRole('button', { name: '重新计算并保存建议' }));
    expect(state.revise.mutate.mock.calls[1][0].client_request_id).toBe(first.client_request_id);
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('allows a stale pending card to be recalculated against the current ledger without confirming it', () => {
    state.query.data.ledger.version = 3;
    render(<SimulationLedger />);
    expect(screen.getByRole('button', { name: '核对并确认模拟买入' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '修改数量 / 限价' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '重新计算并保存建议' }));
    expect(state.revise.mutate.mock.calls[0][0]).toMatchObject({ account_version: 3, suggestion_version: 1 });
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('does not submit an invalid revised quantity or price', () => {
    render(<SimulationLedger />);
    fireEvent.click(screen.getByRole('button', { name: '修改数量 / 限价' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('本次数量', { exact: true }), { target: { value: '1.5' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '重新计算并保存建议' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('数量须为正整数');
    fireEvent.change(within(dialog).getByLabelText('本次数量', { exact: true }), { target: { value: '300' } });
    fireEvent.change(within(dialog).getByLabelText('本次限价', { exact: true }), { target: { value: '0' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '重新计算并保存建议' }));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('限价');
    expect(state.revise.mutate).not.toHaveBeenCalled();
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

  it('opens the entry form before pending cards and exposes disclosure state', () => {
    render(<SimulationLedger />);
    const button = screen.getByRole('button', { name: '记录模拟买入' });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    const form = screen.getByRole('region', { name: '记录手动建议' });
    expect(button).toHaveAttribute('aria-controls', form.id);
    expect(form.compareDocumentPosition(screen.getByRole('region', { name: '模拟建议' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('recovers a committed revision after a lost response without confirming it', () => {
    const { rerender } = render(<SimulationLedger />);
    fireEvent.click(screen.getByRole('button', { name: '修改数量 / 限价' }));
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText('本次数量', { exact: true }), { target: { value: '300' } });
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '重新计算并保存建议' }));
    const request = state.revise.mutate.mock.calls[0][0];
    const child: Suggestion = { ...card, suggestion_id: 'revised-card', client_request_id: request.client_request_id, replaces_suggestion_id: card.suggestion_id, suggestion_version: 2, quantity: 300, estimated_cash_delta: '-305.00' };
    state.query.data.ledger.suggestions = [{ ...card, status: 'cancelled' }, child];
    state.revise = { ...state.revise, error: new Error('网络响应丢失'), isError: true };
    rerender(<SimulationLedger />);
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '查看已保存的新建议' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByLabelText('本次数量', { exact: true })).toHaveValue('300');
    expect(within(dialog).getByRole('checkbox')).not.toBeChecked();
    expect(within(dialog).getByRole('button', { name: '确认模拟买入' })).toBeDisabled();
    expect(state.confirm.mutate).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('checkbox'));
    fireEvent.click(within(dialog).getByRole('button', { name: '确认模拟买入' }));
    expect(state.confirm.mutate).toHaveBeenCalledWith(expect.objectContaining({ suggestion_id: 'revised-card', suggestion_version: 2 }), expect.any(Object));
  });

  it('does not let a confirmation portal cover a different tab after browser navigation', () => {
    const { rerender } = render(<SimulationLedger />);
    fireEvent.click(screen.getByRole('button', { name: '修改数量 / 限价' }));
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText('本次数量', { exact: true }), { target: { value: '300' } });
    rerender(<SimulationLedger section="holdings" />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    rerender(<SimulationLedger section="trading" />);
    expect(within(screen.getByRole('dialog')).getByLabelText('本次数量', { exact: true })).toHaveValue('300');
    expect(state.confirm.mutate).not.toHaveBeenCalled();
  });

});
