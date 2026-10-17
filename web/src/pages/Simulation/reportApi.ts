import { api } from '@/api/client';
import type { LedgerPosition } from './api';

export interface ReportRequest { client_request_id: string; account_version: number }
export interface ReportSnapshot {
  account_id: string; version: number; as_of: string;
  cash: string; available_cash: string; reserved_cash: string; realized_pnl: string;
  positions: LedgerPosition[]; sellable_basis: string;
}
export interface ReportSource {
  source_id: string; kind: 'ledger_snapshot' | 'closing_calendar' | 'closing_price' | 'live_quote' | 'announcement' | 'analyst_research' | 'stock_news' | 'community_search'; title: string; reference: string;
  as_of: string; snapshot_version?: number | null; fetched_at?: string | null; evidence?: Record<string, unknown> | null;
}
export interface AnnouncementEvidence {
  id?: string | null; paper_code: string; ann_date?: string | null; end_date?: string | null;
  ann_type?: string | null; event_category?: string | null; title?: string | null; url?: string | null; pdf_path?: string | null;
}
export interface AnalystReportEvidence {
  id?: string | null; title?: string | null; institution?: string | null; authors?: string | null;
  published_date?: string | null; report_type?: string | null; rating?: string | null;
  target_price?: string | null; symbol: string; url?: string | null;
}
export interface StockNewsEvidence {
  id: string; title: string; summary?: string | null; published_at?: string | null;
  publisher?: string | null; url: string;
  // Legacy categories keep earlier frozen reports readable; new reports contain news only.
  category: 'news' | 'xueqiu' | 'tieba'; source_ids: string[];
}
export interface CurrentQuoteEvidence {
  price: string; currency: 'CNY'; as_of: string; fetched_at: string;
  provider_updated_at?: string | null; freshness: 'fresh' | 'stale' | 'future';
  freshness_as_of: string; source_ids: string[];
}
export interface RealtimePnlEvidence {
  status: 'calculated' | 'pending_verification'; market_value: string | null;
  unrealized_pnl: string | null; currency: 'CNY'; as_of: string | null;
  reason: string | null; source_ids: string[];
}
export interface PositionCheck {
  code: string; exchange: 'SH' | 'SZ' | 'BJ'; name: string; fact: string;
  closing_price?: { trade_date: string; close: string; as_of: string; fetched_at: string; source_ids: string[] } | null;
  current_quote?: CurrentQuoteEvidence | null;
  realtime_pnl?: RealtimePnlEvidence | null;
  announcements?: AnnouncementEvidence[] | null; analyst_reports?: AnalystReportEvidence[] | null;
  news?: StockNewsEvidence[] | null;
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
  return (await api.post<ReportDetail>(base, request, { timeout: 35000 })).data;
}
export async function getReport(id: string, signal?: AbortSignal): Promise<ReportDetail> {
  return (await api.get<ReportDetail>(`${base}/${id}`, { signal, timeout: 15000 })).data;
}
export async function getReports(before?: number, signal?: AbortSignal): Promise<ReportPage> {
  return (await api.get<ReportPage>(base, { params: { limit: 20, before }, signal, timeout: 15000 })).data;
}
