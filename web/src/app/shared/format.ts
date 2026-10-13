import { Pipe, PipeTransform } from '@angular/core';

// Presentation only. Values arrive as decimal strings from the API and are
// converted to numbers solely to format them; nothing here does arithmetic
// whose result is shown as money.

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const SIGNED = new Intl.NumberFormat('en-US', {
  // A zero change carries no sign: "+0.00" would claim a gain.
  minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: 'exceptZero',
});

function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  const number = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

/** 1234.5 -> $1,234.50 */
@Pipe({ name: 'money' })
export class MoneyPipe implements PipeTransform {
  transform(value: string | number | null | undefined): string {
    const number = toNumber(value);
    return number === null ? '—' : USD.format(number);
  }
}

/** A gain or loss always carries its sign, never just a colour: +1,234.50.
 *  The sign is the pipe's job; the arrow beside it is Delta's. */
@Pipe({ name: 'signedMoney' })
export class SignedMoneyPipe implements PipeTransform {
  transform(value: string | number | null | undefined): string {
    const number = toNumber(value);
    return number === null ? '—' : SIGNED.format(number);
  }
}

/** +12.3% — whole numbers once a tiny cost basis makes it four digits. */
@Pipe({ name: 'percent' })
export class PercentPipe implements PipeTransform {
  transform(value: string | number | null | undefined): string {
    const number = toNumber(value);
    if (number === null) {
      return '—';
    }
    const digits = Math.abs(number) >= 1000 ? 0 : 1;
    const text = number.toFixed(digits);
    return `${number > 0 && Number(text) !== 0 ? '+' : ''}${text}%`;
  }
}

/** Trim trailing zeros from a share count: "10.0000" -> "10", "2.50" -> "2.5".
 *  Done on the string so no precision is lost on the way. */
@Pipe({ name: 'shares' })
export class SharesPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (value === null || value === undefined || value === '') {
      return '—';
    }
    return trimShares(value);
  }
}

export function trimShares(value: string): string {
  if (!value.includes('.')) {
    return value;
  }
  return value.replace(/0+$/, '').replace(/\.$/, '');
}

/** Mar 12, 2026, 2:05 PM in the reader's own time zone. */
@Pipe({ name: 'when' })
export class WhenPipe implements PipeTransform {
  private static readonly format = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  });

  transform(value: string | null | undefined): string {
    if (!value) {
      return '';
    }
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : WhenPipe.format.format(date);
  }
}

export type Direction = 'gain' | 'loss' | 'flat' | 'unknown';

/** Label a change so it reads without relying on colour. */
export function movement(value: string | number | null | undefined):
    { direction: Direction; arrow: string; word: string } {
  const number = toNumber(value);
  if (number === null) {
    return { direction: 'unknown', arrow: '', word: 'not priced yet' };
  }
  if (number > 0) {
    return { direction: 'gain', arrow: '▲', word: 'up' };
  }
  if (number < 0) {
    return { direction: 'loss', arrow: '▼', word: 'down' };
  }
  return { direction: 'flat', arrow: '–', word: 'no change' };
}

export const FORMAT_PIPES = [MoneyPipe, SignedMoneyPipe, PercentPipe, SharesPipe, WhenPipe] as const;
