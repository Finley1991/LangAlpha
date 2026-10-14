// Display rounding never writes back to the authoritative total cost.
export function displayAverage(cost: string, quantity: number): string {
  const [whole, fraction = ''] = cost.split('.');
  const units = BigInt(whole + fraction);
  const denominator = BigInt(quantity) * 10n ** BigInt(fraction.length);
  const cents = (2n * units * 100n + denominator) / (2n * denominator);
  return `${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`;
}
