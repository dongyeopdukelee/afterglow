import { test, expect } from '@playwright/test';

const comedyIntent = {
  genres: ['Comedy'], year_min: 1990, year_max: 1999,
  year_min_inclusive: true, year_max_inclusive: true,
  title_keywords: [], unsupported_constraints: [],
};

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
  await page.getByRole('button', { name: 'Next →', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Film 12', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Next →', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '← Previous', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
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
  await page.getByRole('button', { name: 'Browse all films' }).click();
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
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
    await page.getByRole('button', { name: 'Browse all films' }).click();
    await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
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
