import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { TradePage } from '../../core/models';
import { Trades, pageNumber } from './trades';

const EMPTY: TradePage = {
  trades: [], page: 1, pages: 1, total: 0, page_size: 25,
  realized_total: '0', closed_positions: [],
};

describe('Trades', () => {
  let backend: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => backend.verify());

  it('falls back to the first page for a hand-edited ?page=', () => {
    expect(pageNumber(undefined)).toBe(1);
    expect(pageNumber('3')).toBe(3);
    expect(pageNumber('0')).toBe(1);
    expect(pageNumber('-2')).toBe(1);
    expect(pageNumber('1.5')).toBe(1);
    expect(pageNumber('abc')).toBe(1);
  });

  it('loads once per page, not again when the answer arrives', () => {
    const fixture = TestBed.createComponent(Trades);
    fixture.detectChanges();
    backend.expectOne('/api/trades?page=1').flush(EMPTY);
    fixture.detectChanges();
    backend.expectNone('/api/trades?page=1');
    expect(fixture.nativeElement.textContent).toContain('No trades yet');

    fixture.componentRef.setInput('page', '2');
    fixture.detectChanges();
    backend.expectOne('/api/trades?page=2').flush({ ...EMPTY, page: 2 });
  });

  it('shows what a sale earned and leaves a buy blank', () => {
    const fixture = TestBed.createComponent(Trades);
    fixture.detectChanges();
    const trade = {
      id: 1, symbol: 'AAPL', company_name: 'Apple Inc.', shares: '4.0000', price: '200.00',
      total_value: '800.00', cost_basis: '150.00', opening: false,
      traded_at: '2026-10-01T15:00:00Z',
    };
    backend.expectOne('/api/trades?page=1').flush({
      ...EMPTY, total: 2, realized_total: '200.00',
      trades: [
        { ...trade, side: 'sell', gain: '200.00' },
        { ...trade, id: 2, side: 'buy', gain: null, cost_basis: null },
      ],
    });
    fixture.detectChanges();
    const rows = fixture.nativeElement.querySelectorAll('tbody tr');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('Sold');
    expect(rows[0].textContent).toContain('+200.00');
    expect(rows[1].textContent).toContain('Bought');
    expect(rows[1].querySelector('app-delta')).toBeNull();
  });
});
