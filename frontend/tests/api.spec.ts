import { test, expect } from '@playwright/test';
import { detail, results } from '../src/api';

const movie = { id: 1, title: 'A Known Comedy', release_year: 1995, genres: ['Comedy'] };
const intent = {
  genres: ['Comedy'], year_min: 1990, year_max: 1999,
  year_min_inclusive: true, year_max_inclusive: true,
  title_keywords: ['love'], unsupported_constraints: [],
};

test('catalog and recommendation decoding follow the public API response contracts', () => {
  expect(results({ items: [movie] }, 'catalog')).toEqual({ items: [movie] });
  expect(results({ items: [movie], intent }, 'recommendations')).toEqual({ items: [movie], intent });
  const nullableYears = { ...intent, year_min: null, year_max: null };
  expect(results({ items: [], intent: nullableYears }, 'recommendations').intent).toEqual(nullableYears);
});

for (const field of Object.keys(intent)) {
  test(`recommendations require the API intent field ${field}`, () => {
    const incomplete: Record<string, unknown> = { ...intent };
    delete incomplete[field];
    expect(() => results({ items: [], intent: incomplete }, 'recommendations')).toThrow('unexpected response');
  });
}

for (const field of ['genres', 'title_keywords', 'unsupported_constraints']) {
  for (const [name, value] of [
    ['null', null], ['non-string item', [123]], ['empty item', ['']],
    ['long item', ['x'.repeat(201)]], ['too many items', Array<string>(11).fill('Comedy')],
  ] as const) {
    test(`intent rejects ${name} in ${field}`, () => {
      expect(() => results({ items: [], intent: { ...intent, [field]: value } }, 'recommendations')).toThrow('unexpected response');
    });
  }
}

for (const field of ['genres', 'unsupported_constraints']) {
  test(`intent rejects blank ${field}`, () => {
    expect(() => results({ items: [], intent: { ...intent, [field]: ['  '] } }, 'recommendations')).toThrow('unexpected response');
  });
}

test('title keywords retain literal whitespace and accept Unicode at the API length limit', () => {
  const literal = { ...intent, title_keywords: [' ', '🎬'.repeat(200)] };
  expect(results({ items: [], intent: literal }, 'recommendations').intent).toEqual(literal);
});

for (const field of ['year_min', 'year_max']) {
  for (const value of ['banana', 1995.5, 0, 10000]) {
    test(`intent rejects invalid ${field}: ${value}`, () => {
      expect(() => results({ items: [], intent: { ...intent, [field]: value } }, 'recommendations')).toThrow('unexpected response');
    });
  }
}

for (const field of ['year_min_inclusive', 'year_max_inclusive']) {
  for (const value of [null, 'true', 1]) {
    test(`intent rejects non-boolean ${field}: ${value}`, () => {
      expect(() => results({ items: [], intent: { ...intent, [field]: value } }, 'recommendations')).toThrow('unexpected response');
    });
  }
}

test('intent rejects reversed bounds and extra fields forbidden by MovieIntent', () => {
  for (const invalid of [{ ...intent, year_min: 2000 }, { ...intent, arbitrary: 'filter' }]) {
    expect(() => results({ items: [], intent: invalid }, 'recommendations')).toThrow('unexpected response');
  }
});

for (const kind of ['catalog', 'recommendations', 'detail'] as const) {
  test(`${kind} requires integer or null movie release years`, () => {
    const decode = (release_year: number | null) => {
      const card = { ...movie, release_year };
      return kind === 'detail' ? detail({ ...card, synopsis: 'Full synopsis.' })
        : results({ items: [card], intent }, kind).items[0];
    };
    expect(decode(1995).release_year).toBe(1995);
    expect(decode(null).release_year).toBeNull();
    expect(() => decode(1995.5)).toThrow(/unexpected/);
  });
}
