import { createHash } from 'node:crypto';
import { test, expect } from '@playwright/test';
import type { Locator, Request, Response } from '@playwright/test';

const comedyIntent = {
  genres: ['Comedy'], year_min: 1990, year_max: 1999,
  year_min_inclusive: true, year_max_inclusive: true,
  title_keywords: [], unsupported_constraints: [],
};

const metropolisSha256 = '18601f3133cb00c8af5849852991a44575f683ccdb9b8926c261f193f7254ee7';
const charadeSha256 = '0bc01a747de463d751f49654e3fac1ec6957494d1b0ef3a64327a8b343c04fb0';

async function expectPosterDigest(response: Response | undefined, expected: string) {
  expect(response).toBeDefined();
  expect(createHash('sha256').update(await response!.body()).digest('hex')).toBe(expected);
}

async function expectDecodedPosters(images: Locator, count: number) {
  await expect.poll(() => images.evaluateAll((elements, expected) =>
    elements.length === expected && elements.every(image =>
      image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0
    ), count
  )).toBe(true);
}

// A shareable seed makes the real shuffled catalog reproducible. This worked
// example places Film 00 first, Film 00–11 on page one, and Film 12–13 next.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (location.protocol === 'http:' && !new URL(location.href).searchParams.has('seed')) {
      history.replaceState(null, '', '/?seed=332');
    }
  });
});

test('keyboard visitors can skip directly to the catalog', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to films' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('region', { name: 'Movie results' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: /Film 00/ })).toBeFocused();
});

test('search describes its supported filters without redundant navigation', async ({ page }) => {
  await page.goto('/');
  const search = page.getByRole('textbox', { name: 'What would you like to watch?' });
  await expect(search).toHaveAccessibleDescription('Find a film by genre, year, or a word you remember from the title.');
  await expect(page.getByRole('link', { name: 'The collection', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Find your next film', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Find your next one', exact: true })).toHaveCount(0);
});

test('a discovery seed keeps pages stable and a different seed changes the selection', async ({ page }) => {
  await page.goto('/?seed=1');
  await expect(page.locator('.movie-card')).toHaveCount(12);
  const firstPage = await page.locator('.movie-card h3').allTextContents();
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.locator('.movie-card')).toHaveCount(2);
  const secondPage = await page.locator('.movie-card h3').allTextContents();
  expect(new Set([...firstPage, ...secondPage]).size).toBe(14);
  await page.getByRole('button', { name: '← Previous', exact: true }).click();
  await expect(page.locator('.movie-card')).toHaveCount(12);
  expect(await page.locator('.movie-card h3').allTextContents()).toEqual(firstPage);
  await page.goto('/?seed=2');
  await expect(page.locator('.movie-card')).toHaveCount(12);
  expect(await page.locator('.movie-card h3').allTextContents()).not.toEqual(firstPage);
});

test('hero films are static and a failed poster has an intentional fallback', async ({ page, isMobile }) => {
  await page.route('**/posters/**', route => route.abort());
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await expect(page.locator('.cinema-ticket button, .cinema-ticket a')).toHaveCount(0);
  if (!isMobile) {
    await expect(page.locator('.ticket-back')).toContainText('Metropolis');
    await expect(page.locator('.ticket-front')).toContainText('Charade');
    await expect(page.locator('.cinema-ticket .poster-art')).toHaveCount(0);
    await expect(page.locator('.cinema-ticket .art')).toHaveCount(2);
  }
  await expect(page.getByText('For the love of film.', { exact: true })).toHaveCount(0);
});

test('a full page fetches twelve posters and a fresh page load fetches them again', async ({ page }) => {
  const tag = `reload-${crypto.randomUUID()}`;
  const counter = `/movies/_poster-fixture/${tag}`;
  await page.setExtraHTTPHeaders({ 'X-Poster-Test': tag });
  const responses: Response[] = [];
  page.on('response', response => {
    const path = new URL(response.url()).pathname;
    if (/^\/movies\/\d+\/poster$/.test(path)) {
      responses.push(response);
    }
  });
  for (const load of [1, 2]) {
    if (load === 1) await page.goto('/');
    else await page.reload();
    await expectDecodedPosters(page.locator('.movie-card img'), 12);
    expect(await (await page.request.get(counter)).json()).toBe(load * 12);
    expect(responses).toHaveLength(load * 12);
    const current = responses.slice(-12);
    expect(new Set(current.map(response => new URL(response.url()).pathname)).size).toBe(12);
    expect(current.every(response => response.headers()['cache-control'] === 'no-store')).toBe(true);
    for (const [title, path, digest] of [
      ['Film 00', '/movies/1/poster', metropolisSha256],
      ['Film 01', '/movies/2/poster', charadeSha256],
    ]) {
      const card = page.locator('.movie-card').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
      await expect(card.locator('img')).toHaveAttribute('src', path);
      await expectPosterDigest(current.find(response => new URL(response.url()).pathname === path), digest);
    }
  }
  await expect(page.getByRole('region', { name: 'Image credits' })).toContainText('This product uses the TMDB API but is not endorsed or certified by TMDB.');
});

test('each detail opening fetches its own poster while the card is pending', async ({ page }) => {
  const tag = `detail-${crypto.randomUUID()}`;
  const counter = `/movies/_poster-fixture/${tag}`;
  await page.setExtraHTTPHeaders({ 'X-Poster-Test': tag });
  try {
    const cardRequest = page.waitForRequest(request => new URL(request.url()).pathname === '/movies/1/poster');
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await cardRequest;
    await expect.poll(async () => (await page.request.get(counter)).json()).toBe(12);
    const openings = [
      { film: '00', id: 1, digest: metropolisSha256 },
      { film: '00', id: 1, digest: metropolisSha256 },
      { film: '01', id: 2, digest: charadeSha256 },
    ];
    for (const [index, { film, id, digest }] of openings.entries()) {
      const card = page.locator('.movie-card').filter({ has: page.getByRole('heading', { name: `Film ${film}`, exact: true }) });
      if (id === 1) {
        expect(await card.locator('img').evaluate(image => (image as HTMLImageElement).complete)).toBe(false);
      }
      const detailPoster = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === `/movies/${id}/poster` && url.searchParams.has('detail');
      });
      await card.click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toContainText(`The complete synopsis for film ${film}.`);
      await expectDecodedPosters(dialog.locator('img'), 1);
      await expectPosterDigest(await detailPoster, digest);
      expect(await (await page.request.get(counter)).json()).toBe(13 + index);
      await dialog.getByRole('button', { name: 'Close movie details' }).click();
    }
  } finally {
    await page.request.post(`${counter}/release`);
  }
});

test('two tabs share three stalled card slots and keep detail and catalog requests available', async ({ page, context }) => {
  const tag = `stalled-${crypto.randomUUID()}`;
  const counter = `/movies/_poster-fixture/${tag}`;
  await context.setExtraHTTPHeaders({ 'X-Poster-Test': tag });
  const otherTab = await context.newPage();
  let searchPoster: Response | undefined;
  otherTab.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname === '/movies/14/poster' && !url.search) searchPoster = response;
  });
  try {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.movie-card')).toHaveCount(12);
    await expect.poll(async () => (await page.request.get(counter)).json()).toBe(3);
    await otherTab.goto('/?seed=2', { waitUntil: 'domcontentloaded' });
    await expect(otherTab.locator('.movie-card')).toHaveCount(12, { timeout: 2000 });

    for (const tab of [page, otherTab]) {
      const card = tab.locator('.movie-card').first();
      const title = await card.getByRole('heading').innerText();
      await card.click();
      const dialog = tab.getByRole('dialog');
      await expect(dialog).toContainText(`The complete synopsis for ${title.toLowerCase()}.`, { timeout: 2000 });
      await expectDecodedPosters(dialog.locator('img'), 1);
      await dialog.getByRole('button', { name: 'Close movie details' }).click();
    }
    expect(await (await page.request.get(counter)).json()).toBe(5);

    await otherTab.getByRole('button', { name: 'Next →', exact: true }).click();
    await expect(otherTab.locator('.movie-card')).toHaveCount(2, { timeout: 2000 });
    await otherTab.getByRole('button', { name: '← Previous', exact: true }).click();
    await expect(otherTab.locator('.movie-card')).toHaveCount(12, { timeout: 2000 });
    await otherTab.getByRole('textbox').fill('Film 13');
    await otherTab.getByRole('button', { name: 'Find a film' }).click();
    await expect(otherTab.locator('.movie-card h3')).toHaveText(['Film 13'], { timeout: 2000 });
    expect(await (await page.request.get(counter)).json()).toBe(5);
    await page.close();
    await expect.poll(async () => (await otherTab.request.get(counter)).json()).toBe(6);
  } finally {
    await otherTab.request.post(`${counter}/release`);
  }
  await expectDecodedPosters(otherTab.locator('.movie-card img'), 1);
  await expect(otherTab.locator('.movie-card img')).toHaveAttribute('src', '/movies/14/poster');
  await expectPosterDigest(searchPoster, charadeSha256);
});

for (const mode of ['shared', 'local'] as const) {
  test(`pagination aborts stalled card requests with ${mode} poster slots`, async ({ page }) => {
    if (mode === 'local') {
      await page.addInitScript(() => Object.defineProperty(navigator, 'locks', { value: undefined }));
    }
    const tag = `stalled-pagination-${crypto.randomUUID()}`;
    const counter = `/movies/_poster-fixture/${tag}`;
    await page.setExtraHTTPHeaders({ 'X-Poster-Test': tag });
    const cardRequests: Request[] = [];
    const aborted: Request[] = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (request.resourceType() === 'image' && /^\/movies\/\d+\/poster$/.test(url.pathname) && !url.search) {
        cardRequests.push(request);
      }
    });
    page.on('requestfailed', request => {
      if (cardRequests.includes(request) && request.failure()?.errorText === 'net::ERR_ABORTED') {
        aborted.push(request);
      }
    });
    try {
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await expect.poll(async () => (await page.request.get(counter)).json()).toBe(3);
      expect(cardRequests).toHaveLength(3);
      expect(aborted).toHaveLength(0);

      const originalDocument = await page.evaluateHandle(() => document);
      await page.getByRole('button', { name: 'Next →', exact: true }).click();
      await expect(page.locator('.movie-card h3')).toHaveText(['Film 12', 'Film 13']);
      expect(await originalDocument.evaluate(original => original === document)).toBe(true);
      await expect.poll(async () => (await page.request.get(counter)).json()).toBe(5);
      await expect.poll(() => aborted.map(request => request.url()).sort()).toEqual(
        cardRequests.slice(0, 3).map(request => request.url()).sort()
      );
      expect(cardRequests.slice(3).map(request => new URL(request.url()).pathname).sort()).toEqual([
        '/movies/13/poster', '/movies/14/poster',
      ]);
    } finally {
      await page.request.post(`${counter}/release`);
    }
    await expectDecodedPosters(page.locator('.movie-card img'), 2);
    expect(await (await page.request.get(counter)).json()).toBe(5);
  });
}

for (const mode of ['unavailable', 'rejected'] as const) {
  test(`local poster slots keep images available when Web Locks are ${mode}`, async ({ page }) => {
    await page.addInitScript(mode => {
      Object.defineProperty(navigator, 'locks', {
        value: mode === 'unavailable' ? undefined : {
          request: () => Promise.reject(new DOMException('Lock unavailable', 'SecurityError')),
        },
      });
    }, mode);
    const tag = `stalled-local-${crypto.randomUUID()}`;
    const counter = `/movies/_poster-fixture/${tag}`;
    await page.setExtraHTTPHeaders({ 'X-Poster-Test': tag });
    try {
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      await expect.poll(async () => (await page.request.get(counter)).json()).toBe(3);
      await page.getByRole('button', { name: /Film 00/ }).click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toContainText('The complete synopsis for film 00.');
      await expectDecodedPosters(dialog.locator('img'), 1);
      expect(await (await page.request.get(counter)).json()).toBe(4);
      await dialog.getByRole('button', { name: 'Close movie details' }).click();
    } finally {
      await page.request.post(`${counter}/release`);
    }
    await expectDecodedPosters(page.locator('.movie-card img'), 12);
    expect(await (await page.request.get(counter)).json()).toBe(13);
  });
}

test('exhausted poster fetches leave the films usable without more image retries', async ({ page }) => {
  const tag = `exhausted-${crypto.randomUUID()}`;
  const counter = `/movies/_poster-fixture/${tag}`;
  await page.setExtraHTTPHeaders({ 'X-Poster-Test': tag });
  const settled: URL[] = [];
  const statuses: number[] = [];
  page.on('response', response => {
    const url = new URL(response.url());
    if (/^\/movies\/\d+\/poster$/.test(url.pathname)) {
      settled.push(url);
      statuses.push(response.status());
    }
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await expect.poll(() => settled.length).toBe(12);
  expect(new Set(settled.map(url => url.pathname)).size).toBe(12);
  await expect(page.locator('.movie-card img')).toHaveCount(0);
  await expect(page.locator('.movie-card .art-label')).toHaveCount(12);
  // The backend owns the three-attempt S3 cap. Rendering a failed image must
  // not start another retry loop in the browser.
  await page.getByRole('textbox').fill('a query');
  expect(await (await page.request.get(counter)).json()).toBe(36);
  await page.getByRole('button', { name: /Film 00/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('The complete synopsis for film 00.');
  await expect.poll(() => settled.length).toBe(13);
  expect(settled[12].searchParams.has('detail')).toBe(true);
  await expect(dialog.locator('img')).toHaveCount(0);
  await expect(dialog.locator('.art-label')).toBeVisible();
  expect(statuses).toEqual(Array(13).fill(503));
  expect(await (await page.request.get(counter)).json()).toBe(39);
});

test('visitor browses real API pages and reads a full synopsis', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Film 11', exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('collection.png'), fullPage: true });
  const firstCard = page.getByRole('button', { name: /Film 00/ });
  await firstCard.focus();
  await firstCard.press('Enter');
  await expect(page.getByRole('dialog')).toContainText('The complete synopsis for film 00.');
  await page.getByRole('button', { name: 'Close movie details' }).press('Enter');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(firstCard).toBeFocused();
  await firstCard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(firstCard).toBeFocused();
  await page.getByRole('button', { name: 'Next →', exact: true }).focus();
  await page.getByRole('button', { name: 'Next →', exact: true }).press('Enter');
  await expect(page.getByRole('heading', { name: 'Film 12', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Next →', exact: true })).toBeDisabled();
  await expect(page.getByRole('heading', { name: 'The collection' })).toBeFocused();
  await page.getByRole('button', { name: '← Previous', exact: true }).focus();
  await page.getByRole('button', { name: '← Previous', exact: true }).press('Enter');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'The collection' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(firstCard).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('a visitor can rapidly reopen the same synopsis with the keyboard', async ({ page }) => {
  await page.goto('/');
  const card = page.getByRole('button', { name: /Film 00/ });
  await expect(card).toBeVisible();
  await card.focus();
  for (let attempt = 0; attempt < 8; attempt++) {
    await card.press('Enter');
    await expect(page.getByRole('dialog')).toBeVisible();
    if (attempt % 2 === 0) {
      await page.getByRole('button', { name: 'Close movie details' }).press('Enter');
    } else {
      await page.keyboard.press('Escape');
    }
    // Check the visitor-visible state without waiting for a queued close event.
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(card).toBeFocused();
  }
});

test('search sends the entered request and exposes interpreted filters', async ({ page }) => {
  // Inference is an external boundary: the returned card and intent are an
  // explicit API contract fixture, not evidence of live model accuracy.
  await page.route('**/recommendations', async route => {
    expect(route.request().postDataJSON()).toEqual({ query: '90s comedies' });
    await route.fulfill({ json: {
      items: [{ id: 1, title: 'A Known Comedy', release_year: 1995, genres: ['Comedy'] }],
      intent: comedyIntent,
    } });
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await page.getByRole('textbox').fill('90s comedies');
  await page.getByRole('button', { name: 'Find a film' }).click();
  await expect(page.getByRole('heading', { name: 'A Known Comedy' })).toBeVisible();
  await expect(page.getByText(/Interpreted filters:/)).toHaveText('Interpreted filters: genres: Comedy · year ≥ 1990 · year ≤ 1999');
  await page.getByRole('button', { name: 'Browse all films' }).focus();
  await page.getByRole('button', { name: 'Browse all films' }).press('Enter');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'The collection' })).toBeFocused();
});

for (const status of [422, 429, 503]) {
  test(`search handles HTTP ${status} without showing stale cards`, async ({ page }) => {
    await page.route('**/recommendations', route => route.fulfill({ status, json: { detail: 'controlled test failure' } }));
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
    await page.getByRole('textbox').fill('some films');
    await page.getByRole('button', { name: 'Find a film' }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Browse all films' }).focus();
    await page.getByRole('button', { name: 'Browse all films' }).press('Enter');
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'The collection' })).toBeFocused();
  });
}

for (const direction of ['Next →', '← Previous']) {
  test(`keyboard ${direction} retains a results focus target through loading and failure`, async ({ page }) => {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
    if (direction === '← Previous') {
      await page.getByRole('button', { name: 'Next →', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Film 12', exact: true })).toBeVisible();
    }
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route(url => url.pathname === '/movies', async route => {
      await pending;
      await route.fulfill({ status: 500, json: { detail: 'controlled catalog failure' } });
    });
    const control = page.getByRole('button', { name: direction, exact: true });
    const heading = page.getByRole('heading', { name: 'The collection' });
    await control.focus();
    await control.press('Enter');
    await expect(page.getByRole('status')).toContainText('Opening the collection');
    await expect(heading).toBeFocused();
    release();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(heading).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Try again' })).toBeFocused();
  });
}

test('keyboard return to browsing keeps focus through loading and failure', async ({ page }) => {
  await page.route('**/recommendations', route => route.fulfill({ json: { items: [], intent: comedyIntent } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await page.getByRole('textbox').fill('no matches');
  await page.getByRole('textbox').press('Enter');
  await expect(page.getByRole('status')).toContainText('No films found');
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route(url => url.pathname === '/movies', async route => {
    await pending;
    await route.fulfill({ status: 500, json: { detail: 'controlled catalog failure' } });
  });
  await page.getByRole('button', { name: 'Browse all films' }).focus();
  await page.getByRole('button', { name: 'Browse all films' }).press('Enter');
  const heading = page.getByRole('heading', { name: 'The collection' });
  await expect(page.getByRole('status')).toContainText('Opening the collection');
  await expect(heading).toBeFocused();
  release();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(heading).toBeFocused();
});

for (const mode of ['catalog', 'search']) {
  for (const outcome of ['movies', 'empty', 'failure']) {
    test(`keyboard ${mode} retry keeps focus through loading and ${outcome}`, async ({ page }) => {
      let retrying = false;
      let release!: () => void;
      const pending = new Promise<void>(resolve => { release = resolve; });
      await page.route(url => url.pathname === (mode === 'catalog' ? '/movies' : '/recommendations'), async route => {
        if (!retrying) {
          await route.fulfill({ status: 500, json: { detail: 'controlled failure' } });
          return;
        }
        await pending;
        if (outcome === 'failure') {
          await route.fulfill({ status: 500, json: { detail: 'controlled retry failure' } });
        } else if (outcome === 'movies' && mode === 'catalog') {
          await route.continue();
        } else {
          await route.fulfill({ json: {
            items: outcome === 'empty' ? [] : [{ id: 1, title: 'A Known Comedy', release_year: 1995, genres: ['Comedy'] }],
            ...(mode === 'search' ? { intent: comedyIntent } : {}),
          } });
        }
      });
      await page.goto('/');
      if (mode === 'search') {
        await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
        await page.getByRole('textbox').fill('comedies');
        await page.getByRole('textbox').press('Enter');
      }
      await expect(page.getByRole('alert')).toBeVisible();
      const heading = page.getByRole('heading', { name: mode === 'catalog' ? 'The collection' : 'Results for “comedies”' });
      await page.getByRole('button', { name: 'Try again' }).focus();
      retrying = true;
      await page.getByRole('button', { name: 'Try again' }).press('Enter');
      await expect(page.getByRole('status')).toContainText(mode === 'catalog' ? 'Opening the collection' : 'Looking through the catalog');
      await expect(heading).toBeFocused();
      release();
      if (outcome === 'failure') await expect(page.getByRole('alert')).toBeVisible();
      else if (outcome === 'empty') await expect(page.getByRole('status')).toContainText('No films found');
      else await expect(page.getByRole('heading', { name: mode === 'catalog' ? 'Film 00' : 'A Known Comedy', exact: true })).toBeVisible();
      await expect(heading).toBeFocused();
    });
  }
}

for (const source of ['textbox', 'submit', 'example']) {
  test(`keyboard ${source} search hands focus to results before disabling controls`, async ({ page }) => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route('**/recommendations', async route => {
      await pending;
      await route.fulfill({ json: { items: [], intent: comedyIntent } });
    });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
    await expect(page.locator('body')).toBeFocused();
    await page.getByRole('textbox').fill('comedies');
    const control = source === 'textbox' ? page.getByRole('textbox') : page.getByRole('button', {
      name: source === 'submit' ? 'Find a film' : 'Comedies from the 1990s',
    });
    await control.focus();
    await control.press('Enter');
    const heading = page.getByRole('heading', { name: source === 'example' ? 'Results for “Comedies from the 1990s”' : 'Results for “comedies”' });
    await expect(page.getByRole('status')).toContainText('Looking through the catalog');
    await expect(heading).toBeFocused();
    if (source === 'textbox') await page.getByRole('textbox').focus();
    release();
    await expect(page.getByRole('status')).toContainText('No films found');
    await expect(source === 'textbox' ? page.getByRole('textbox') : heading).toBeFocused();
  });
}

test('empty and malformed search responses do not masquerade as movies', async ({ page }) => {
  let malformed = false;
  await page.route('**/recommendations', route => route.fulfill({ json: { items: malformed ? [null] : [], intent: comedyIntent } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await page.getByRole('textbox').fill('no matches');
  await page.getByRole('button', { name: 'Find a film' }).click();
  await expect(page.getByRole('status')).toContainText('No films found');
  malformed = true;
  await page.getByRole('button', { name: 'Find a film' }).click();
  await expect(page.getByRole('alert')).toContainText('unexpected response');
});

for (const bounds of [
  { name: 'after', year_min: 2000, year_max: null, year_min_inclusive: false, year_max_inclusive: true, label: 'year > 2000' },
  { name: 'since', year_min: 2000, year_max: null, year_min_inclusive: true, year_max_inclusive: true, label: 'year ≥ 2000' },
  { name: 'before', year_min: null, year_max: 2000, year_min_inclusive: true, year_max_inclusive: false, label: 'year < 2000' },
  { name: 'until', year_min: null, year_max: 2000, year_min_inclusive: true, year_max_inclusive: true, label: 'year ≤ 2000' },
  { name: 'exclusive range', year_min: 1990, year_max: 2000, year_min_inclusive: false, year_max_inclusive: false, label: 'year > 1990 · year < 2000' },
  { name: 'in', year_min: 2000, year_max: 2000, year_min_inclusive: true, year_max_inclusive: true, label: 'year ≥ 2000 · year ≤ 2000' },
]) {
  test(`interpreted ${bounds.name} filters preserve comparison semantics`, async ({ page }) => {
    const { name, label, ...years } = bounds;
    await page.route('**/recommendations', route => route.fulfill({ json: {
      items: [], intent: { ...comedyIntent, genres: [], ...years },
    } }));
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
    await page.getByRole('textbox').fill(`${name} 2000`);
    await page.getByRole('button', { name: 'Find a film' }).click();
    await expect(page.getByText(/Interpreted filters:/)).toHaveText(`Interpreted filters: ${label}`);
  });
}

for (const [name, payload] of [
  ['missing intent', { items: [] }],
  ['null intent', { items: [], intent: null }],
  ['invalid intent', { items: [], intent: { ...comedyIntent, year_min: 'banana' } }],
] as const) {
  test(`search rejects ${name} and can recover`, async ({ page }) => {
    await page.route('**/recommendations', route => route.fulfill({ json: payload }));
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
    await page.getByRole('textbox').fill('comedies');
    await page.getByRole('button', { name: 'Find a film' }).click();
    await expect(page.getByRole('alert')).toContainText('unexpected response');
    await expect(page.getByText(/Interpreted filters:/)).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toHaveCount(0);
    await page.getByRole('button', { name: 'Browse all films' }).click();
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  });
}
