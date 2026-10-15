import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import { cancelOrder, cancelSuggestion, confirmSuggestion, createSuggestion, getEvents, getLedger, getRecord, reviseSuggestion, type RecordKind } from './api';

export function useLedger() {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: queryKeys.simulation.all });
  const query = useQuery({ queryKey: queryKeys.simulation.ledger(), queryFn: ({ signal }) => getLedger(signal), refetchInterval: 30000 });
  const events = useInfiniteQuery({
    queryKey: queryKeys.simulation.events(), initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => getEvents(pageParam, signal),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: !!query.data?.ledger,
  });
  // No automatic mutation retry: stale cards need a new explicit decision.
  const create = useMutation({ mutationFn: createSuggestion, onSuccess: refresh, retry: false });
  const confirm = useMutation({ mutationFn: confirmSuggestion, onSuccess: refresh, onError: refresh, retry: false });
  const revise = useMutation({ mutationFn: reviseSuggestion, onSuccess: refresh, onError: refresh, retry: false });
  const dismiss = useMutation({ mutationFn: cancelSuggestion, onSuccess: refresh, onError: refresh, retry: false });
  const cancel = useMutation({ mutationFn: cancelOrder, onSuccess: refresh, onError: refresh, retry: false });
  return { query, events, create, confirm, revise, dismiss, cancel };
}

export function useSimulationRecord(kind?: RecordKind, id?: string) {
  return useQuery({ queryKey: queryKeys.simulation.record(kind, id), enabled: !!kind && !!id,
    queryFn: ({ signal }) => getRecord(kind!, id!, signal) });
}
