import { test, expect } from '@playwright/test';

test('visitor browses real API pages and reads a full synopsis', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Film 11', exact: true })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('collection.png'), fullPage: true });
  await page.getByRole('button', { name: /Film 00/ }).click();
  await expect(page.getByRole('dialog')).toContainText('The complete synopsis for film 00.');
  await page.getByRole('button', { name: 'Close movie details' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
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
      intent: { genres: ['Comedy'], year_min: 1990, year_max: 1999 },
    } });
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await page.getByRole('textbox').fill('90s comedies');
  await page.getByRole('button', { name: 'Find a film' }).click();
  await expect(page.getByRole('heading', { name: 'A Known Comedy' })).toBeVisible();
  await expect(page.getByText(/Interpreted filters:/)).toContainText('Comedy');
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
  await page.route('**/recommendations', route => route.fulfill({ json: malformed ? { items: [null] } : { items: [] } }));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Film 00', exact: true })).toBeVisible();
  await page.getByRole('textbox').fill('no matches');
  await page.getByRole('button', { name: 'Find a film' }).click();
  await expect(page.getByRole('status')).toContainText('No films found');
  malformed = true;
  await page.getByRole('button', { name: 'Find a film' }).click();
  await expect(page.getByRole('alert')).toContainText('unexpected response');
});
