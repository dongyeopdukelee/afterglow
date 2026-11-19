// Leave HTTP/1.1 connections available for catalog and synopsis requests.
const maximumDownloads = 4;
type Download = { start: () => void; started: boolean; finished: boolean };
const waiting: Download[] = [];
let active = 0;
let scheduled = false;

function drainSoon() {
  if (scheduled) return;
  scheduled = true;
  queueMicrotask(() => {
    scheduled = false;
    while (active < maximumDownloads && waiting.length > 0) {
      const download = waiting.shift()!;
      download.started = true;
      active += 1;
      download.start();
    }
  });
}

export function schedulePoster(start: () => void): () => void {
  const download = { start, started: false, finished: false };
  waiting.push(download);
  // Defer dispatch so React's development effect cleanup can cancel a queued
  // mount before it starts a duplicate HTTP request.
  drainSoon();
  return () => {
    if (download.finished) return;
    download.finished = true;
    if (download.started) active -= 1;
    else waiting.splice(waiting.indexOf(download), 1);
    drainSoon();
  };
}
