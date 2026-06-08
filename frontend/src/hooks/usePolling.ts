import { useEffect, useRef, useState } from 'react';

interface PollingState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
}

/**
 * Polls `fetcher` every `intervalMs` until `shouldStop(data)` returns true.
 * Re-runs whenever any value in `deps` changes, restarting the poll loop.
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  shouldStop: (data: T) => boolean,
  deps: React.DependencyList,
): PollingState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);

  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let firstRun = true;

    async function tick() {
      if (firstRun) {
        firstRun = false;
        setLoading(true);
        setError(null);
      }
      try {
        const result = await fetcherRef.current();
        if (cancelled) return;
        setData(result);
        setLoading(false);
        if (!shouldStop(result)) {
          timer = setTimeout(tick, intervalMs);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err : new Error(String(err)));
        setLoading(false);
      }
    }

    tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading };
}
