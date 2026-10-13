import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { Leaderboard } from './leaderboard';

describe('Leaderboard', () => {
  let backend: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => backend.verify());

  it('shows where the reader would come before they have joined', () => {
    const fixture = TestBed.createComponent(Leaderboard);
    fixture.detectChanges();
    backend.expectOne('/api/leaderboard').flush({
      starting_cash: '50000',
      standing: { rank: 2, total_value: '55000.00', percent_return: '10', opted_in: false,
                  participants: 1, as_of: '2026-10-01T15:00:00Z' },
      rows: [{ rank: 1, name: 'Ahead', total_value: '60000.00', percent_return: '20', is_you: false }],
    });
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Where you would come if you joined');
    expect(text).toContain('You are not on the leaderboard');
    expect(fixture.nativeElement.querySelector('tr.is-you')).toBeNull();
    expect(fixture.nativeElement.querySelectorAll('tbody tr').length).toBe(1);
  });

  it('says so when nobody is listed and the account is not valued', () => {
    const fixture = TestBed.createComponent(Leaderboard);
    fixture.detectChanges();
    backend.expectOne('/api/leaderboard').flush({ starting_cash: '50000', standing: null, rows: [] });
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Nobody has joined yet');
    expect(text).toContain('has not been valued yet');
  });
});
