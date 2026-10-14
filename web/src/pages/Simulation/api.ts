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
