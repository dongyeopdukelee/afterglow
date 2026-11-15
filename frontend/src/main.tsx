import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { detail, request, results } from './api';
import type { Detail, Intent, Movie } from './api';
import './style.css';

const examples = ['Comedies from the 1990s', 'Thrillers released after 2000', 'Movies with love in the title'];
const pageSize = 12;
function message(error: unknown): string {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}
function interpretedFilters(intent: Intent): string {
  return [
    intent.genres.length > 0 ? `genres: ${intent.genres.join(', ')}` : '',
    intent.year_min === null ? '' : `year ${intent.year_min_inclusive ? '≥' : '>'} ${intent.year_min}`,
    intent.year_max === null ? '' : `year ${intent.year_max_inclusive ? '≤' : '<'} ${intent.year_max}`,
    intent.title_keywords.length > 0 ? `title keywords: ${intent.title_keywords.join(', ')}` : '',
    intent.unsupported_constraints.length > 0 ? `unsupported constraints: ${intent.unsupported_constraints.join(', ')}` : '',
  ].filter(Boolean).join(' · ');
}

function MovieDialog({ movie, close }: { movie: Movie; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [full, setFull] = useState<Detail>();
  const [error, setError] = useState('');
  function dismiss() {
    // Native close restores focus but queues its close event. Clear React
    // selection now so a rapid reopen cannot reuse the still-selected movie.
    dialog.current?.close();
    close();
  }
  useEffect(() => {
    const controller = new AbortController();
    dialog.current?.showModal();
    request(`/movies/${movie.id}`, controller.signal).then(detail).then(setFull)
      .catch(e => { if (!controller.signal.aborted) setError(message(e)); });
    return () => controller.abort();
  }, [movie.id]);
  return <dialog ref={dialog} onCancel={event => { event.preventDefault(); dismiss(); }} aria-labelledby="movie-title">
    <button className="close" aria-label="Close movie details" onClick={dismiss}>×</button>
    <p className="eyebrow">From the catalog</p><h2 id="movie-title">{movie.title}</h2>
    <p className="muted">{movie.release_year ?? 'Year unavailable'} · {movie.genres.join(' / ') || 'Genres unavailable'}</p>
    {error ? <p role="alert">{error}</p> : full ? <p className="synopsis">{full.synopsis}</p> : <p role="status">Loading synopsis…</p>}
  </dialog>;
}

function App() {
  const [query, setQuery] = useState('');
  const [movies, setMovies] = useState<Movie[]>([]);
  const [intent, setIntent] = useState<Intent>();
  const [selected, setSelected] = useState<Movie>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'browse' | 'search'>('browse');
  const [offset, setOffset] = useState(0);
  const [submitted, setSubmitted] = useState('');
  const active = useRef<AbortController | null>(null);
  const resultsHeading = useRef<HTMLHeadingElement>(null);
  async function load(search: string | undefined, nextOffset = 0) {
    if (document.activeElement?.closest('main')) resultsHeading.current?.focus();
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setLoading(true); setError(''); setMovies([]); setIntent(undefined);
    setMode(search === undefined ? 'browse' : 'search'); setOffset(nextOffset); setSubmitted(search ?? '');
    const timeout = window.setTimeout(() => controller.abort('timeout'), 120_000);
    try {
      const data = results(await request(search === undefined
        ? `/movies?limit=${pageSize}&offset=${nextOffset}` : '/recommendations', controller.signal, search),
        search === undefined ? 'catalog' : 'recommendations');
      if (!controller.signal.aborted) { setMovies(data.items); setIntent(data.intent); }
    } catch (e) {
      if (!controller.signal.aborted) setError(message(e));
      else if (controller.signal.reason === 'timeout') setError('Search took too long. Please try again or browse the catalog.');
    } finally {
      window.clearTimeout(timeout);
      if (active.current === controller) setLoading(false);
    }
  }
  useEffect(() => { void load(undefined); return () => active.current?.abort(); }, []);
  function search(value: string) { const trimmed = value.trim(); if (trimmed) { setQuery(trimmed); void load(trimmed); } }
  return <>
    <header><a className="brand" href="/">afterglow<span>✳</span></a><span className="header-note">A little less scrolling. A little more cinema.</span></header>
    <main>
      <section className="hero"><p className="eyebrow">Something worth watching</p>
        <h1>Find a film.<br/><em>Stay for the feeling.</em></h1>
        <p className="intro">Tell us what you're in the mood for. Discover something in the catalog, in your own words.</p>
        <form onSubmit={e => { e.preventDefault(); search(query); }}>
          <label className="sr-only" htmlFor="query">What would you like to watch?</label>
          <div className="search-box"><input id="query" value={query} onChange={e => setQuery(e.target.value)} maxLength={1000} placeholder="A comedy from the 90s…" autoComplete="off" required />
          <button className="primary" type="submit" disabled={loading || !query.trim()}>Find a film <span aria-hidden="true">↗</span></button></div>
        </form>
        <div className="examples">{examples.map(example => <button key={example} disabled={loading} onClick={() => search(example)}>{example} <span aria-hidden="true">↗</span></button>)}</div>
        <p className="availability">Early preview · Genre, year and title searches work today. Plot and mood matching are coming next.</p>
      </section>
      <section className="catalog" aria-label="Movie results" aria-busy={loading}>
        <div className="section-heading"><div><p className="eyebrow">{mode === 'browse' ? 'The collection' : 'Your discoveries'}</p><h2 ref={resultsHeading} tabIndex={-1}>{mode === 'browse' ? 'Start somewhere unexpected.' : `Results for “${submitted}”`}</h2></div>
          {mode === 'search' && <button className="text-button" onClick={() => void load(undefined)}>Browse all films</button>}</div>
        {intent && <p className="interpreted">Interpreted filters: {interpretedFilters(intent)}</p>}
        {loading ? <p className="status" role="status">{mode === 'search' ? 'Looking through the catalog…' : 'Opening the collection…'}</p>
          : error ? <div className="status" role="alert"><p>{error}</p><button onClick={() => void load(mode === 'browse' ? undefined : submitted, offset)}>Try again</button></div>
          : movies.length === 0 ? <p className="status" role="status">No films found. Try another request, or browse the collection.</p>
          : <div className="movie-grid">{movies.map((movie, index) => <button className="movie-card" key={movie.id} onClick={() => setSelected(movie)}>
            <div className={`art art-${index % 4}`} aria-hidden="true"><span>{movie.title.slice(0, 1)}</span><small>AFTERGLOW COLLECTION</small></div>
            <div className="card-content"><span className="year">{movie.release_year ?? 'Year unavailable'}</span><h3>{movie.title}</h3><p>{movie.genres.slice(0, 3).join(' / ') || 'Discover this film'}</p><span className="read-more">Read synopsis ↗</span></div>
          </button>)}</div>}
        {mode === 'browse' && !loading && !error && <nav className="pagination" aria-label="Catalog pages"><button disabled={offset === 0} onClick={() => void load(undefined, Math.max(0, offset - pageSize))}>← Previous</button><span>Page {Math.floor(offset / pageSize) + 1}</span><button disabled={movies.length < pageSize} onClick={() => void load(undefined, offset + pageSize)}>Next →</button></nav>}
      </section>
    </main>
    <footer><span className="brand">afterglow✳</span><p>Built for the joy of finding a film.</p><p>Catalog and plot summaries: <a href="https://www.cs.cmu.edu/~ark/personas/" target="_blank" rel="noreferrer">CMU Movie Summary Corpus</a> · <a href="https://creativecommons.org/licenses/by-sa/3.0/us/" target="_blank" rel="noreferrer">CC BY-SA 3.0 US</a>. Decorative covers are not official movie posters.</p></footer>
    {selected && <MovieDialog key={selected.id} movie={selected} close={() => setSelected(undefined)} />}
  </>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
