import { api } from '@/api/client';

export interface OpeningHolding {
  code: string;
  exchange: 'SH' | 'SZ' | 'BJ';
  name: string;
  quantity: number;
  total_cost: string;
  purchased_on: string;
  acquisition_dates?: string[];
  batch_details_known?: boolean;
}

export interface OpeningRequest {
  opening_date: string;
  initial_cash: string;
  cost_includes_fees: boolean;
  holdings: OpeningHolding[];
}

export interface OpeningSnapshot extends OpeningRequest {
  account_id: string;
  version: 1;
  currency: 'CNY';
  mode: 'simulation';
  created_at: string;
  historical_fees: null;
  valuation_status: 'unverified';
}

export interface OpeningResponse { snapshot: OpeningSnapshot | null }
const endpoint = '/api/v1/users/me/simulation/opening';
export async function getOpening(signal?: AbortSignal): Promise<OpeningResponse> {
  return (await api.get<OpeningResponse>(endpoint, { signal, timeout: 15000 })).data;
}
export async function initializeOpening(request: OpeningRequest): Promise<OpeningResponse> {
  return (await api.post<OpeningResponse>(endpoint, request, { timeout: 15000 })).data;
}

export interface Fees {
  version: string; commission_rate: string; minimum_commission: string; sell_tax_rate: string;
  transfer_rate: string; slippage_bps: string; description: string;
}
export interface SuggestionRequest {
  client_request_id: string; account_version: number; code: string; exchange: 'SH' | 'SZ'; name: string;
  side: 'buy' | 'sell'; quantity: number; limit_price: string; reason: string; counterargument: string; expires_at: string;
}
export interface Suggestion extends SuggestionRequest {
  suggestion_id: string; suggestion_version: number; status: 'pending' | 'confirmed' | 'cancelled' | 'expired';
  replaces_suggestion_id?: string | null;
  fees: Fees; created_at: string; estimated_cash_delta: string; estimated_cash_after: string;
  estimated_fees: { total: string }; reference_status: 'unverified';
}
export interface Order extends Omit<Suggestion, 'status'> {
  order_id: string; status: 'pending' | 'filled' | 'cancelled' | 'expired'; confirmed_at: string;
  reserved_cash: string; execution_status: string;
}
export interface Fill {
  fill_id: string; order_id: string; suggestion_id: string; side: 'buy' | 'sell'; code: string; exchange: string; name: string;
  price: string; quantity: number; cash_delta: string; cash_after: string; realized_pnl: string; created_at: string;
  fees: { total: string; version: string }; quote: { as_of: string; source: string; source_kind: 'provider' | 'test'; evidence_id: string };
}
export interface LedgerPosition {
  code: string; exchange: string; name: string; quantity: number; available_quantity: number; total_cost: string;
  lots: { quantity: number; acquired_on: string; date_basis: string }[];
}
export interface Ledger {
  account_id: string; version: number; as_of: string; cash: string; available_cash: string; reserved_cash: string;
  realized_pnl: string; positions: LedgerPosition[]; suggestions: Suggestion[]; orders: Order[]; fills: Fill[];
  fees: Fees; sellable_basis: string; history_limit: number; market_data_status: 'unverified';
}
export interface SimulationEvent {
  event_id: number; event_type: string; cash_delta: string; created_at: string;
  payload: { suggestion_id?: string; replaces_suggestion_id?: string; order_id?: string; fill_id?: string; account_version?: number; actor?: string; quote_evidence_id?: string };
}
export interface EventPage { events: SimulationEvent[]; next_cursor: number | null }
const base = '/api/v1/users/me/simulation';
export async function getLedger(signal?: AbortSignal): Promise<{ ledger: Ledger | null }> {
  return (await api.get<{ ledger: Ledger | null }>(`${base}/ledger`, { signal, timeout: 15000 })).data;
}
export async function createSuggestion(request: SuggestionRequest): Promise<{ result: Suggestion }> {
  return (await api.post<{ result: Suggestion }>(`${base}/suggestions`, request, { timeout: 15000 })).data;
}
export async function confirmSuggestion(request: { suggestion_id: string; suggestion_version: number; account_version: number; acknowledge_simulation: true }): Promise<{ result: Order }> {
  const { suggestion_id, ...body } = request;
  return (await api.post<{ result: Order }>(`${base}/suggestions/${suggestion_id}/confirm`, body, { timeout: 15000 })).data;
}
export interface RevisionRequest {
  suggestion_id: string; client_request_id: string; suggestion_version: number;
  account_version: number; quantity: number; limit_price: string;
}
export async function reviseSuggestion(request: RevisionRequest): Promise<{ result: Suggestion }> {
  const { suggestion_id, ...body } = request;
  return (await api.post<{ result: Suggestion }>(`${base}/suggestions/${suggestion_id}/revise`, body, { timeout: 15000 })).data;
}
export async function cancelSuggestion(id: string): Promise<{ result: Suggestion }> {
  return (await api.post<{ result: Suggestion }>(`${base}/suggestions/${id}/cancel`, undefined, { timeout: 15000 })).data;
}
export async function cancelOrder(id: string): Promise<{ result: Order }> {
  return (await api.post<{ result: Order }>(`${base}/orders/${id}/cancel`, undefined, { timeout: 15000 })).data;
}
export async function getEvents(before?: number, signal?: AbortSignal): Promise<EventPage> {
  return (await api.get<EventPage>(`${base}/events`, { params: { before, limit: 50 }, signal, timeout: 15000 })).data;
}
export type RecordKind = 'suggestions' | 'orders' | 'fills';
export async function getRecord(kind: RecordKind, id: string, signal?: AbortSignal): Promise<{ result: Suggestion | Order | Fill }> {
  return (await api.get<{ result: Suggestion | Order | Fill }>(`${base}/${kind}/${id}`, { signal, timeout: 15000 })).data;
}
