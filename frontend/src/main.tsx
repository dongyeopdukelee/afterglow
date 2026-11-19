import { StrictMode, useEffect, useId, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { detail, request, results } from './api';
import type { Detail, Intent, Movie } from './api';
import { posterFor, posterUrl } from './posters';
import { schedulePoster } from './poster-queue';
import './style.css';

const examples = ['Comedies from the 1990s', 'Thrillers released after 2000', 'Movies with love in the title'];
const shortExamples = ['90s comedies', 'Thrillers after 2000', '“Love” in the title'];
const pageSize = 12;
function discoverySeed(): number {
  const requested = new URLSearchParams(location.search).get('seed');
  if (requested !== null && /^\d+$/.test(requested)) {
    const value = Number(requested);
    if (Number.isSafeInteger(value) && value <= 2_147_483_647) return value;
  }
  return crypto.getRandomValues(new Uint32Array(1))[0] & 0x7fffffff;
}
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

function Arrow({ diagonal = false }: { diagonal?: boolean }) {
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className="arrow-icon">
    <path d={diagonal ? 'M6 18 18 6M6 6h12v12' : 'M5 12h14m-6-6 6 6-6 6'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>;
}

function FilmArtwork({ variant = 0 }: { variant?: number }) {
  return <div className={`art art-${variant % 4}`} aria-hidden="true">
    <div className="art-orbit" /><div className="art-disc" /><div className="art-line" />
    <span className="art-label">AFTERGLOW<br />THE COLLECTION</span>
    <span className="art-edition">{String(variant % 4 + 1).padStart(2, '0')} / FILM</span>
  </div>;
}

function MovieArtwork({ movie, hero = false, detailId }: { movie: Pick<Movie, 'id' | 'title' | 'release_year'>; hero?: boolean; detailId?: string }) {
  const poster = hero ? posterFor(movie) : undefined;
  const isDetail = detailId !== undefined;
  const query = isDetail ? `?detail=${encodeURIComponent(detailId)}` : '';
  const url = hero ? (poster && posterUrl(poster)) : `/movies/${movie.id}/poster${query}`;
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);
  const finish = useRef<() => void>(() => {});
  const image = useRef<HTMLImageElement | null>(null);
  useEffect(() => {
    if (hero) return;
    setFailed(false);
    setReady(false);
    const cancel = schedulePoster(isDetail, () => setReady(true), () => setFailed(true));
    finish.current = cancel;
    return () => {
      // React clears DOM refs before passive cleanup. Keep the element until
      // here so leaving a page also cancels its native image request.
      image.current?.removeAttribute('src');
      image.current = null;
      cancel();
    };
  }, [url, hero, isDetail]);
  return url && !failed && (hero || ready)
    ? <div className="art poster-art"><img ref={element => { if (element) image.current = element; }} src={url} alt={`${movie.title} (${movie.release_year ?? 'year unavailable'}) poster`} loading="eager" onLoad={() => finish.current()} onError={() => { finish.current(); setFailed(true); }} /></div>
    : <FilmArtwork variant={movie.id} />;
}

function MovieDialog({ movie, close }: { movie: Movie; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const detailId = useId();
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
    <div className="detail-art"><MovieArtwork movie={movie} detailId={detailId} /></div>
    <div className="detail-content">
      <p className="eyebrow">A closer look</p><h2 id="movie-title">{movie.title}</h2>
      <p className="muted">{movie.release_year ?? 'Year unavailable'} · {movie.genres.join(' / ') || 'Genres unavailable'}</p>
      <h3 className="synopsis-heading">The story</h3>
      {error ? <p role="alert">{error}</p> : full ? <p className="synopsis">{full.synopsis}</p> : <p role="status">Loading synopsis…</p>}
    </div>
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
  const [seed] = useState(discoverySeed);
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
        ? `/movies?limit=${pageSize}&offset=${nextOffset}&seed=${seed}` : '/recommendations', controller.signal, search),
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
    <a className="skip-link" href="#collection">Skip to films</a>
    <header>
      <a className="brand" href="/" aria-label="Afterglow home">afterglow</a>
    </header>
    <main>
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy">
        <h1 id="hero-title">Less searching.<br/><span>More cinema.</span></h1>
        <p className="intro" id="search-intro">Find a film by genre, year, or a word you remember from the title.</p>
        <form className="search-form" onSubmit={e => { e.preventDefault(); search(query); }}>
          <label className="sr-only" htmlFor="query">What would you like to watch?</label>
          <div className="search-box">
          <svg className="search-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" stroke="currentColor" strokeWidth="1.7"/><path d="m16 16 4 4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>
          <input id="query" value={query} onChange={e => setQuery(e.target.value)} maxLength={1000} placeholder="A comedy from the 90s…" autoComplete="off" aria-describedby="search-intro" required />
          <button className="primary" type="submit" disabled={loading || !query.trim()}>Find a film <Arrow /></button></div>
        </form>
        <div className="examples"><span className="examples-label">Try a little inspiration</span>{examples.map((example, index) => <button key={example} aria-label={example} disabled={loading} onClick={() => search(example)}><span className="example-long">{example}</span><span className="example-short" aria-hidden="true">{shortExamples[index]}</span><Arrow diagonal /></button>)}</div>
        </div>
        <div className="hero-scene" aria-label="Films from the collection">
          <div className="scene-halo" />
          <figure className="cinema-ticket ticket-back"><MovieArtwork movie={{ id: 15060, title: 'Metropolis', release_year: 1927 }} hero /><figcaption><strong>Metropolis</strong><span>1927 · Science fiction</span></figcaption></figure>
          <figure className="cinema-ticket ticket-front"><MovieArtwork movie={{ id: 36695, title: 'Charade', release_year: 1963 }} hero /><figcaption><strong>Charade</strong><span>1963 · Mystery</span></figcaption></figure>
        </div>
      </section>
      <section className="catalog" id="collection" tabIndex={-1} aria-label="Movie results" aria-busy={loading}>
        <div className="section-heading"><div>{mode === 'search' && <p className="eyebrow">Your discoveries</p>}<h2 ref={resultsHeading} className={mode === 'browse' ? 'eyebrow collection-title' : undefined} tabIndex={-1}>{mode === 'browse' ? 'The collection' : `Results for “${submitted}”`}</h2></div>
          {mode === 'search' && <button className="text-button" onClick={() => void load(undefined)}>Browse all films <Arrow /></button>}</div>
        {intent && <p className="interpreted">Interpreted filters: {interpretedFilters(intent)}</p>}
        {loading ? <p className="status" role="status">{mode === 'search' ? 'Looking through the catalog…' : 'Opening the collection…'}</p>
          : error ? <div className="status" role="alert"><p>{error}</p><button onClick={() => void load(mode === 'browse' ? undefined : submitted, offset)}>Try again</button></div>
          : movies.length === 0 ? <p className="status" role="status">No films found. Try another request, or browse the collection.</p>
          : <div className="movie-grid">{movies.map(movie => <button className="movie-card" key={movie.id} onClick={() => setSelected(movie)}>
            <MovieArtwork movie={movie} />
            <div className="card-content"><span className="year">{movie.release_year ?? 'Year unavailable'}</span><h3>{movie.title}</h3><p>{movie.genres.slice(0, 3).join(' / ') || 'Discover this film'}</p><span className="read-more">Read synopsis <Arrow diagonal /></span></div>
          </button>)}</div>}
        {mode === 'browse' && !loading && !error && <nav className="pagination" aria-label="Catalog pages"><button disabled={offset === 0} onClick={() => void load(undefined, Math.max(0, offset - pageSize))}>← Previous</button><span>Page {Math.floor(offset / pageSize) + 1}</span><button disabled={movies.length < pageSize} onClick={() => void load(undefined, offset + pageSize)}>Next →</button></nav>}
      </section>
    </main>
    <footer><div className="footer-top"><span className="brand">afterglow</span><p>Good films leave a little afterglow.</p></div><p className="attribution">Catalog and plot summaries: <a href="https://www.cs.cmu.edu/~ark/personas/" target="_blank" rel="noreferrer">CMU Movie Summary Corpus</a> · <a href="https://creativecommons.org/licenses/by-sa/3.0/us/" target="_blank" rel="noreferrer">CC BY-SA 3.0 US</a>.</p>
      <section className="image-credits" aria-labelledby="image-credits-title">
        <h2 id="image-credits-title">Image credits</h2>
        <a href="https://www.themoviedb.org" target="_blank" rel="noreferrer"><img src="/credits/tmdb.svg" alt="TMDB" width="120" /></a>
        <p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p>
        <p>Additional public-domain posters come from Wikimedia Commons. Abstract artwork fills unavailable covers.</p>
      </section>
    </footer>
    {selected && <MovieDialog key={selected.id} movie={selected} close={() => setSelected(undefined)} />}
  </>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
