import { useCallback, useEffect, useState } from "react";

// Runs `load(signal)` whenever `deps` change; exposes data/error/loading and a reload().
export function useApi(load, deps) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState((previous) => ({ ...previous, loading: true, error: null }));
    load(controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setState({ data, error: null, loading: false });
      })
      .catch((error) => {
        if (!controller.signal.aborted && error.name !== "AbortError") {
          setState({ data: null, error, loading: false });
        }
      });
    return () => controller.abort();
  }, [...deps, version]);

  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { ...state, reload };
}
