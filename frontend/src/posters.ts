import manifest from '../../src/movie_db_llm/posters.json';
import type { Movie } from './api';

export const posters = manifest;
export type Poster = typeof manifest[number];
const byFilm = new Map(posters.map(poster => [`${poster.title}\0${poster.year}`, poster]));

export function posterFor(movie: Pick<Movie, 'title' | 'release_year'>): Poster | undefined {
  return byFilm.get(`${movie.title}\0${movie.release_year}`);
}

export function posterUrl(poster: Poster): string {
  return `/posters/${poster.filename}`;
}
