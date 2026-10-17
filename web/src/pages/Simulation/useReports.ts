import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import { getLedger } from './api';
import { generateReport, getReport, getReports } from './reportApi';

export function useReports(selectedId?: string) {
  const client = useQueryClient();
  const ledger = useQuery({ queryKey: queryKeys.simulation.ledger(), queryFn: ({ signal }) => getLedger(signal) });
  const history = useInfiniteQuery({
    queryKey: queryKeys.simulation.reports(), enabled: !!ledger.data?.ledger,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => getReports(pageParam, signal),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    refetchInterval: 30000,
  });
  const detail = useQuery({
    queryKey: queryKeys.simulation.report(selectedId), enabled: !!selectedId,
    queryFn: ({ signal }) => getReport(selectedId!, signal), refetchInterval: 30000,
  });
  const generate = useMutation({
    mutationFn: generateReport, retry: false,
    onSuccess: (data) => {
      client.setQueryData(queryKeys.simulation.report(data.report.report_id), data);
      void client.invalidateQueries({ queryKey: queryKeys.simulation.all });
    },
    onError: () => { void client.invalidateQueries({ queryKey: queryKeys.simulation.ledger() }); },
  });
  return { ledger, history, detail, generate };
}
