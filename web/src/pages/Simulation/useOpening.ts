import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import { getOpening, initializeOpening } from './api';

export function useOpening() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: queryKeys.simulation.opening(), queryFn: ({ signal }) => getOpening(signal) });
  const mutation = useMutation({
    mutationFn: initializeOpening,
    onSuccess: async (response) => {
      client.setQueryData(queryKeys.simulation.opening(), response);
      await client.invalidateQueries({ queryKey: queryKeys.simulation.ledger() });
    },
  });
  return { query, mutation };
}
