import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { Actions } from './actions';

describe('Actions', () => {
  let backend: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => backend.verify());

  it('reports what happened to each holding, failures included', () => {
    const fixture = TestBed.createComponent(Actions);
    fixture.detectChanges();
    const buttons = fixture.nativeElement.querySelectorAll('.job button') as NodeListOf<HTMLButtonElement>;
    buttons[0].click();
    fixture.detectChanges();
    // Both jobs wait while one runs.
    expect(buttons[0].disabled && buttons[1].disabled).toBeTrue();

    backend.expectOne({ method: 'POST', url: '/api/actions/refresh-prices' }).flush({
      title: 'Price refresh', message: 'Refreshed 1 of 2 prices. 1 could not be updated.',
      kind: 'success', total: 2,
      outcomes: [{ symbol: 'AAPL', ok: true, message: '$210.00' },
                 { symbol: 'NOPE', ok: false, message: 'no price data available' }],
    });
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('Refreshed 1 of 2 prices.');
    expect(fixture.nativeElement.querySelectorAll('.outcomes li').length).toBe(2);
    expect(fixture.nativeElement.querySelector('.outcome-bad').textContent).toContain('Failed —');
    expect(buttons[0].disabled).toBeFalse();
  });

  it('points to buying when there is nothing to update', () => {
    const fixture = TestBed.createComponent(Actions);
    fixture.detectChanges();
    (fixture.nativeElement.querySelectorAll('.job button')[1] as HTMLButtonElement).click();
    backend.expectOne('/api/actions/load-history').flush({
      title: 'Historical data load', kind: 'notice', total: 0, outcomes: [],
      message: 'You have no holdings yet, so there was no history to load.',
    });
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Buy your first shares');
  });
});
