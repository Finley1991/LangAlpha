import { useRef, type KeyboardEvent } from 'react';
import { cn } from '@/lib/utils';

// Keep panels mounted so changing views never discards an unsaved draft or request identity.
export function WorkspaceTabs<T extends string>({ tabs, value, onChange, prefix, label }: {
  tabs: readonly { id: T; label: string }[]; value: string; onChange: (value: T) => void; prefix: string; label: string;
}) {
  const buttons = useRef(new Map<T, HTMLButtonElement>());
  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next: number;
    switch (event.key) {
      case 'ArrowRight': next = (index + 1) % tabs.length; break;
      case 'ArrowLeft': next = (index + tabs.length - 1) % tabs.length; break;
      case 'Home': next = 0; break;
      case 'End': next = tabs.length - 1; break;
      default: return;
    }
    event.preventDefault();
    onChange(tabs[next].id); buttons.current.get(tabs[next].id)?.focus();
  }
  return <div role="tablist" aria-label={label} className="flex border-b border-border gap-1 sm:gap-5">
    {tabs.map((tab, index) => <button key={tab.id} type="button" role="tab"
      id={`${prefix}-tab-${tab.id}`} aria-controls={`${prefix}-panel-${tab.id}`} aria-selected={value === tab.id} tabIndex={value === tab.id ? 0 : -1}
      ref={(element) => { if (element) buttons.current.set(tab.id, element); else buttons.current.delete(tab.id); }}
      onKeyDown={(event) => navigate(event, index)} onClick={() => onChange(tab.id)}
      className={cn('flex-1 sm:flex-none whitespace-nowrap px-2 sm:px-3 py-3 text-sm border-b-2 -mb-px transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-t-md',
        value === tab.id ? 'border-foreground text-foreground font-semibold' : 'border-transparent text-muted-foreground hover:text-foreground')}>{tab.label}</button>)}
  </div>;
}
