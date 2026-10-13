import { MoneyPipe, PercentPipe, SharesPipe, SignedMoneyPipe, movement, trimShares } from './format';

describe('format pipes', () => {
  it('formats money from decimal strings', () => {
    const money = new MoneyPipe();
    expect(money.transform('1234.5')).toBe('$1,234.50');
    expect(money.transform('50000')).toBe('$50,000.00');
    expect(money.transform(null)).toBe('—');
    expect(money.transform('NaN')).toBe('—');
  });

  it('always signs a gain or loss, but never a zero', () => {
    const signed = new SignedMoneyPipe();
    expect(signed.transform('12.5')).toBe('+12.50');
    expect(signed.transform('-3')).toBe('-3.00');
    expect(signed.transform('0')).toBe('0.00');
  });

  it('formats percentages, dropping decimals for huge ones', () => {
    const percent = new PercentPipe();
    expect(percent.transform('6.84')).toBe('+6.8%');
    expect(percent.transform('-0.77')).toBe('-0.8%');
    expect(percent.transform('0')).toBe('0.0%');
    expect(percent.transform('1234.5')).toBe('+1235%');
    expect(percent.transform(null)).toBe('—');
  });

  it('trims share counts without going through a float', () => {
    expect(trimShares('10.0000')).toBe('10');
    expect(trimShares('2.50')).toBe('2.5');
    expect(trimShares('100')).toBe('100');
    expect(trimShares('0.0001')).toBe('0.0001');
    expect(new SharesPipe().transform(null)).toBe('—');
  });

  it('describes a movement in words as well as colour', () => {
    expect(movement('1')).toEqual({ direction: 'gain', arrow: '▲', word: 'up' });
    expect(movement('-1')).toEqual({ direction: 'loss', arrow: '▼', word: 'down' });
    expect(movement('0.00')).toEqual({ direction: 'flat', arrow: '–', word: 'no change' });
    expect(movement(null).direction).toBe('unknown');
  });
});
