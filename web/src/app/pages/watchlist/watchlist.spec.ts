import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { WatchRow } from '../../core/models';
import { Watchlist } from './watchlist';

const AAPL: WatchRow = {
  symbol: 'AAPL', company_name: 'Apple Inc.', price: '200.00', previous_close: '190.00',
  todays_change: '10.00', todays_percent: '5.26', owned: true, added_at: '2026-10-01T15:00:00Z',
};

describe('Watchlist', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<Watchlist>;
  const text = () => fixture.nativeElement.textContent as string;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(Watchlist);
    fixture.detectChanges();
  });

  afterEach(() => backend.verify());

  it('says so when nothing is followed, and offers no refresh', () => {
    backend.expectOne('/api/watchlist').flush({ rows: [], prices_as_of: null });
    fixture.detectChanges();
    expect(text()).toContain('Nothing followed yet');
    expect(text()).not.toContain('Refresh prices');
  });

  it('marks a company that is also held', () => {
    backend.expectOne('/api/watchlist').flush({ rows: [AAPL], prices_as_of: null });
    fixture.detectChanges();
    const row = fixture.nativeElement.querySelector('tbody tr');
    expect(row.textContent).toContain('Held');
    expect(row.textContent).toContain('+10.00');
  });

  it('unfollows, says what happened, and re-reads the list', () => {
    backend.expectOne('/api/watchlist').flush({ rows: [AAPL], prices_as_of: null });
    fixture.detectChanges();
    fixture.nativeElement.querySelector('button[aria-label="Unfollow AAPL"]').click();

    const call = backend.expectOne('/api/watchlist/AAPL');
    expect(call.request.method).toBe('DELETE');
    call.flush({ symbol: 'AAPL', changed: true, message: 'AAPL removed from your watchlist.' });
    backend.expectOne('/api/watchlist').flush({ rows: [], prices_as_of: null });
    fixture.detectChanges();
    expect(text()).toContain('AAPL removed from your watchlist.');
    expect(text()).toContain('Nothing followed yet');
  });

  it('shows why a ticker could not be followed and keeps the list', () => {
    backend.expectOne('/api/watchlist').flush({ rows: [AAPL], prices_as_of: null });
    fixture.detectChanges();
    const input = fixture.nativeElement.querySelector('#follow') as HTMLInputElement;
    input.value = 'NOPE';
    input.dispatchEvent(new Event('input'));
    fixture.nativeElement.querySelector('form').dispatchEvent(new Event('submit'));

    backend.expectOne({ method: 'POST', url: '/api/watchlist' })
      .flush({ detail: 'No company found for NOPE.' }, { status: 400, statusText: 'Bad Request' });
    fixture.detectChanges();
    expect(text()).toContain('No company found for NOPE.');
    expect(fixture.nativeElement.querySelectorAll('tbody tr').length).toBe(1);
  });
});
