type PosterQueue = { slots: string[]; active: number; pending: Set<() => void> };

const cardQueue: PosterQueue = {
  slots: ['afterglow-poster-card-1', 'afterglow-poster-card-2', 'afterglow-poster-card-3'],
  active: 0,
  pending: new Set(),
};
const detailQueue: PosterQueue = {
  slots: ['afterglow-poster-detail'],
  active: 0,
  pending: new Set(),
};

function drainSoon(queue: PosterQueue) {
  queueMicrotask(() => {
    for (const begin of queue.pending) {
      if (queue.active >= queue.slots.length) break;
      queue.pending.delete(begin);
      queue.active += 1;
      begin();
    }
  });
}

export function schedulePoster(detail: boolean, start: () => void): () => void {
  const queue = detail ? detailQueue : cardQueue;
  const waiting = new AbortController();
  let releaseLock: (() => void) | undefined;
  let active = false;
  let finished = false;

  function startDownload() {
    if (waiting.signal.aborted) return;
    waiting.abort();
    start();
  }

  function begin() {
    active = true;
    try {
      const locks = navigator.locks;
      if (!locks) {
        startDownload();
        return;
      }
      for (const slot of queue.slots) {
        void locks.request(slot, { signal: waiting.signal }, () => {
          if (waiting.signal.aborted) return;
          return new Promise<void>(resolve => {
            releaseLock = resolve;
            startDownload();
          });
        }).catch(startDownload);
      }
    } catch {
      startDownload();
    }
  }

  queue.pending.add(begin);
  drainSoon(queue);
  return () => {
    if (finished) return;
    finished = true;
    waiting.abort();
    releaseLock?.();
    if (active) queue.active -= 1;
    else queue.pending.delete(begin);
    drainSoon(queue);
  };
}
