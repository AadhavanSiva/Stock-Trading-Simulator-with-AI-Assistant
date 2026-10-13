import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { StockDetail } from '../../core/models';
import { Stock } from './stock';

describe('Stock', () => {
  let backend: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    // Only the loading is under test; the template needs a full StockDetail.
    TestBed.overrideComponent(Stock, { set: { template: '', imports: [] } });
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => backend.verify());

  it('loads once per symbol, not again when the answer arrives', () => {
    const fixture = TestBed.createComponent(Stock);
    fixture.componentRef.setInput('symbol', 'AAPL');
    fixture.detectChanges();

    backend.expectOne('/api/stocks/AAPL').flush({ symbol: 'AAPL' } as StockDetail);
    fixture.detectChanges();
    backend.expectNone('/api/stocks/AAPL');

    fixture.componentRef.setInput('symbol', 'MSFT');
    fixture.detectChanges();
    backend.expectOne('/api/stocks/MSFT').flush({ symbol: 'MSFT' } as StockDetail);
  });
});
