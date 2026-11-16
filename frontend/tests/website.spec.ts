import { test, expect } from '@playwright/test';

const comedyIntent = {
  genres: ['Comedy'], year_min: 1990, year_max: 1999,
  year_min_inclusive: true, year_max_inclusive: true,
  title_keywords: [], unsupported_constraints: [],
};

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
