export type Movie = {
  id: number;
  title: string;
  release_year: number | null;
  genres: string[];
};
export type Detail = Movie & { synopsis: string };
export type Intent = {
  genres: string[];
  year_min: number | null;
  year_max: number | null;
  year_min_inclusive: boolean;
  year_max_inclusive: boolean;
  title_keywords: string[];
  unsupported_constraints: string[];
};
export type Results = { items: Movie[]; intent?: Intent };

export async function request(path: string, signal: AbortSignal, body?: { query: string; limit: number }): Promise<unknown> {
  const response = await fetch(path, {
    signal,
    ...(body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
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
    && (value.release_year === null || Number.isInteger(value.release_year))
    && Array.isArray(value.genres) && value.genres.every(g => typeof g === 'string');
}
function intentTextList(value: unknown, trim: boolean): value is string[] {
  return Array.isArray(value) && value.length <= 10 && value.every(item => {
    if (typeof item !== 'string') return false;
    const length = [...(trim ? item.trim() : item)].length;
    return length >= 1 && length <= 200;
  });
}
function intentYear(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 9999);
}
function intent(value: unknown): value is Intent {
  return record(value) && Object.keys(value).length === 7
    && intentTextList(value.genres, true)
    && intentYear(value.year_min) && intentYear(value.year_max)
    && (value.year_min === null || value.year_max === null || value.year_min <= value.year_max)
    && typeof value.year_min_inclusive === 'boolean' && typeof value.year_max_inclusive === 'boolean'
    && intentTextList(value.title_keywords, false) && intentTextList(value.unsupported_constraints, true);
}
export function results(value: unknown, kind: 'catalog' | 'recommendations'): Results {
  if (!record(value) || !Array.isArray(value.items) || !value.items.every(movie)) {
    throw new Error('The server returned an unexpected response. Please try again.');
  }
  if (kind === 'catalog') return { items: value.items };
  if (!intent(value.intent)) {
    throw new Error('The server returned an unexpected response. Please try again.');
  }
  return { items: value.items, intent: value.intent };
}
export function detail(value: unknown): Detail {
  if (!record(value) || typeof value.synopsis !== 'string' || !movie(value)) {
    throw new Error('The server returned an unexpected movie. Please try again.');
  }
  return value as Detail;
}
