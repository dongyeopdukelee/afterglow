const cardSlots = ['afterglow-poster-card-1', 'afterglow-poster-card-2', 'afterglow-poster-card-3'];
const detailSlots = ['afterglow-poster-detail'];

export function schedulePoster(detail: boolean, start: () => void, fail: () => void): () => void {
  const waiting = new AbortController();
  let release: () => void = () => {};
  queueMicrotask(() => {
    if (waiting.signal.aborted) return;
    const locks = navigator.locks;
    if (!locks) {
      fail();
      return;
    }
    for (const slot of detail ? detailSlots : cardSlots) {
      void locks.request(slot, { signal: waiting.signal }, () => {
        if (waiting.signal.aborted) return;
        waiting.abort();
        return new Promise<void>(resolve => {
          release = resolve;
          start();
        });
      }).catch(() => {
        if (waiting.signal.aborted) return;
        waiting.abort();
        fail();
      });
    }
  });
  return () => {
    waiting.abort();
    release();
  };
}
