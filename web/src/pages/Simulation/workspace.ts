export type SimulationSection = 'trading' | 'holdings' | 'reports' | 'orders' | 'fills' | 'history' | 'audit' | 'opening';
export const workspaceTabs = [
  { id: 'trading', label: '买卖' }, { id: 'holdings', label: '持仓' }, { id: 'reports', label: '报告' },
  { id: 'records', label: '记录' }, { id: 'opening', label: '期初' },
] as const;
export const recordTabs = [
  { id: 'orders', label: '订单' }, { id: 'fills', label: '成交' },
  { id: 'history', label: '历史建议' }, { id: 'audit', label: '操作审计' },
] as const;

const hashes: Record<SimulationSection, string> = {
  trading: '#simulation-trading', holdings: '#simulation-holdings', reports: '#simulation-reports',
  orders: '#simulation-orders', fills: '#simulation-fills', history: '#simulation-history', audit: '#simulation-audit', opening: '#simulation-opening',
};
export const sectionHash = (section: SimulationSection) => hashes[section];
export const sectionFromHash = (hash: string): SimulationSection => (Object.keys(hashes) as SimulationSection[]).find((key) => hashes[key] === hash) ?? 'trading';
export const sectionGroup = (section: SimulationSection) => recordTabs.some((tab) => tab.id === section) ? 'records' : section;
