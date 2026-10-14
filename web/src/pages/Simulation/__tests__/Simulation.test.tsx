import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import Simulation from '../Simulation';

const { useOpeningMock } = vi.hoisted(() => ({ useOpeningMock: vi.fn() }));
vi.mock('../useOpening', () => ({ useOpening: useOpeningMock }));

describe('opening snapshot', () => {
  it('keeps all reported dates and labels unknown batch allocation', () => {
    useOpeningMock.mockReturnValue({
      query: { isSuccess: true, data: { snapshot: {
        version: 1, opening_date: '2026-10-13', initial_cash: '1000.00', cost_includes_fees: false,
        holdings: [{ code: '000001', exchange: 'SZ', name: '虚构汇总持仓', quantity: 100, total_cost: '1234.5678', purchased_on: '2026-10-10', acquisition_dates: ['2026-10-10', '2026-10-12'], batch_details_known: false }],
      } } },
      mutation: { isPending: false },
    });
    render(<MemoryRouter><Simulation /></MemoryRouter>);
    expect(screen.getByText('2026-10-10、2026-10-12')).toBeInTheDocument();
    expect(screen.getByText('批次数量与成本分配未知')).toBeInTheDocument();
    expect(screen.getByText('1234.5678')).toBeInTheDocument();
    expect(screen.getByText('12.35')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '确认初始化模拟账本' })).not.toBeInTheDocument();
    expect(screen.getByText(/模拟下单暂未开放/)).toBeInTheDocument();
  });
});
