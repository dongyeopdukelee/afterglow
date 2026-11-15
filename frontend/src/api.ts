export type Movie = {
  id: number;
  title: string;
  release_year: number | null;
  genres: string[];
};
export type Detail = Movie & { synopsis: string };
export type Results = { items: Movie[]; intent?: Record<string, unknown> };

const base = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
export async function request(path: string, signal: AbortSignal, query?: string): Promise<unknown> {
  const response = await fetch(base + path, {
    signal,
    ...(query === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    }),
  });
  if (!response.ok) {
    if (response.status === 429) throw new Error('The search allowance has been reached. You can still browse the catalog.');
    if (response.status === 503) throw new Error('Search is temporarily unavailable. Please try again later, or browse the catalog.');
    if (response.status === 404) throw new Error('This movie is no longer available.');
    if (response.status === 422) throw new Error('This request is not supported yet. Try genres, release years, or words in a title.');
    throw new Error('We could not complete that request. Please try again.');
  }
  return response.json();
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function movie(value: unknown): value is Movie {
  return record(value) && Number.isInteger(value.id) && typeof value.title === 'string'
    && (value.release_year === null || typeof value.release_year === 'number')
    && Array.isArray(value.genres) && value.genres.every(g => typeof g === 'string');
}
export function results(value: unknown): Results {
  if (!record(value) || !Array.isArray(value.items) || !value.items.every(movie)) {
    throw new Error('The server returned an unexpected response. Please try again.');
  }
  return { items: value.items, ...(record(value.intent) ? { intent: value.intent } : {}) };
}
export function detail(value: unknown): Detail {
  if (!record(value) || typeof value.synopsis !== 'string' || !movie(value)) {
    throw new Error('The server returned an unexpected movie. Please try again.');
  }
  return value as Detail;
}
