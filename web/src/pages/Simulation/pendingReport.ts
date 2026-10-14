import { z } from 'zod/mini';
import { registerAuthReset } from '@/lib/authResets';
import { userSessionStorage } from '@/lib/userStorage';
import type { ReportRequest } from './reportApi';

const prefix = 'simulation-report-request:';
const schema = z.strictObject({
  client_request_id: z.uuid(),
  account_version: z.int().check(z.gte(1), z.lte(Number.MAX_SAFE_INTEGER)),
});

function read(accountId: string): ReportRequest | undefined {
  const raw = userSessionStorage.getItem(prefix + accountId);
  if (!raw || raw.length > 512) return undefined;
  try {
    const parsed = schema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : undefined;
  } catch { return undefined; }
}

export function pendingReportRequest(accountId: string, version: number): ReportRequest {
  const existing = read(accountId);
  if (existing?.account_version === version) return existing;
  const next = { client_request_id: crypto.randomUUID(), account_version: version };
  userSessionStorage.setItem(prefix + accountId, JSON.stringify(next));
  if (read(accountId)?.client_request_id !== next.client_request_id) {
    // Persist before POST: a blocked store must not turn a reload into a new write.
    throw new Error('浏览器无法保存重试标识，请允许会话存储后再生成报告。');
  }
  return next;
}

export function finishReportRequest(accountId: string, request: ReportRequest) {
  if (read(accountId)?.client_request_id === request.client_request_id) {
    userSessionStorage.removeItem(prefix + accountId);
  }
}

registerAuthReset(() => {
  // Auth has already changed its storage user; remove only our pending metadata.
  try {
    const keys: string[] = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i);
      if (key && (key.startsWith(prefix) || key.includes(':' + prefix))) keys.push(key);
    }
    keys.forEach((key) => sessionStorage.removeItem(key));
  } catch { /* No pending metadata can be read in a blocked store. */ }
});
