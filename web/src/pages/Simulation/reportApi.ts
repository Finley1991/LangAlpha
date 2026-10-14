import { api } from '@/api/client';
import type { LedgerPosition } from './api';

export interface ReportRequest { client_request_id: string; account_version: number }
export interface ReportSnapshot {
  account_id: string; version: number; as_of: string;
  cash: string; available_cash: string; reserved_cash: string; realized_pnl: string;
  positions: LedgerPosition[]; sellable_basis: string;
}
export interface ReportSource {
  source_id: string; kind: 'ledger_snapshot'; title: string; reference: string;
  as_of: string; snapshot_version: number;
}
export interface PositionCheck {
  code: string; exchange: 'SH' | 'SZ' | 'BJ'; name: string; fact: string;
  source_ids: string[]; judgment: string; counterargument: string; trigger: string; missing_data: string[];
}
export interface ReportContent {
  schema_version: 'simulation-report-v1'; report_id: string; report_date: string;
  generated_at: string; cutoff_at: string; expires_at: string; status: 'awaiting_data';
  title: string; summary: string; validity: string; opening_in_future: boolean;
  snapshot: ReportSnapshot; sources: ReportSource[]; checks: PositionCheck[];
  missing_data: string[]; scope: string;
}
export interface ReportDetail {
  report: ReportContent; sequence: number; content_hash: string;
  current_version: number; is_stale: boolean; expired: boolean;
}
export interface ReportSummary {
  report_id: string; sequence: number; report_date: string; generated_at: string; expires_at: string;
  snapshot_version: number; title: string; status: 'awaiting_data'; is_stale: boolean; expired: boolean;
}
export interface ReportPage { items: ReportSummary[]; next_cursor: number | null }
const base = '/api/v1/users/me/simulation/reports';
export async function generateReport(request: ReportRequest): Promise<ReportDetail> {
  return (await api.post<ReportDetail>(base, request, { timeout: 15000 })).data;
}
export async function getReport(id: string, signal?: AbortSignal): Promise<ReportDetail> {
  return (await api.get<ReportDetail>(`${base}/${id}`, { signal, timeout: 15000 })).data;
}
export async function getReports(before?: number, signal?: AbortSignal): Promise<ReportPage> {
  return (await api.get<ReportPage>(base, { params: { limit: 20, before }, signal, timeout: 15000 })).data;
}
