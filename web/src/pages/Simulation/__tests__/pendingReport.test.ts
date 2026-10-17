import { beforeEach, describe, expect, it, vi } from 'vitest';
import { runAuthResets } from '@/lib/authResets';
import { pendingReportRequest, finishReportRequest } from '../pendingReport';

beforeEach(() => { sessionStorage.clear(); });

describe('pending report recovery', () => {
  it('clears acknowledged requests but never clears a newer request', () => {
    const old = pendingReportRequest('account-a', 1);
    const fresh = pendingReportRequest('account-a', 2);
    finishReportRequest('account-a', old);
    expect(pendingReportRequest('account-a', 2)).toEqual(fresh);
    finishReportRequest('account-a', fresh);
    expect(pendingReportRequest('account-a', 2).client_request_id).not.toBe(fresh.client_request_id);
  });

  it('drops only pending report metadata at auth reset', () => {
    const old = pendingReportRequest('account-a', 1);
    sessionStorage.setItem('unrelated-draft', 'keep');
    runAuthResets();
    expect(sessionStorage.getItem('unrelated-draft')).toBe('keep');
    expect(pendingReportRequest('account-a', 1).client_request_id).not.toBe(old.client_request_id);
  });

  it('does not proceed when the retry identity cannot be persisted', () => {
    const blocked = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('full', 'QuotaExceededError'); });
    try { expect(() => pendingReportRequest('account-a', 1)).toThrow('无法保存重试标识'); }
    finally { blocked.mockRestore(); }
  });
});
