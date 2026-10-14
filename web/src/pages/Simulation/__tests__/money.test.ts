// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { displayAverage } from '../money';

describe('opening cost display', () => {
  it.each([
    ['1234.5678', 100, '12.35'],
    ['1.005', 1, '1.01'],
    ['1.00499999', 1, '1.00'],
    ['0', 100, '0.00'],
    ['9999999999999999.99999999', 1, '10000000000000000.00'],
    ['10', 3, '3.33'],
  ])('rounds %s / %s without binary floating point', (cost, quantity, expected) => {
    expect(displayAverage(cost, quantity)).toBe(expected);
  });
});
