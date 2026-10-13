import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { Observable } from 'rxjs';

import { ApiService, errorMessage } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { BuyPreview, Quote } from '../../core/models';
import { NoticeService } from '../../core/notice.service';
import { FORMAT_PIPES } from '../../shared/format';
import { SymbolSearch } from '../../shared/symbol-search';

/** Buying, in three steps: find the stock, choose how many, confirm.
 *  Only the last step writes, and it prices the order afresh. */
@Component({
  selector: 'app-buy',
  imports: [FormsModule, RouterLink, SymbolSearch, ...FORMAT_PIPES],
  templateUrl: './buy.html',
})
export class Buy implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly notices = inject(NoticeService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly symbol = signal('');
  protected readonly quote = signal<Quote | null>(null);
  protected readonly preview = signal<BuyPreview | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected shares = '';

  ngOnInit(): void {
    const symbol = this.route.snapshot.queryParamMap.get('symbol');
    if (symbol) {
      this.symbol.set(symbol.toUpperCase());
      this.lookUp(symbol);
    }
  }

  protected lookUp(symbol: string = this.symbol()): void {
    if (!symbol.trim()) {
      this.error.set('Enter a ticker symbol.');
      return;
    }
    this.run(this.api.quote(symbol.trim()), (quote) => {
      this.quote.set(quote);
      this.symbol.set(quote.symbol);
      this.preview.set(null);
      this.shares = '';
    }, () => {
      // Not a ticker we can price. Someone who typed "apple" wants search.
      this.router.navigate(['/search'], { queryParams: { q: symbol.trim() } });
    });
  }

  protected review(): void {
    const quote = this.quote();
    if (!quote) {
      return;
    }
    this.run(this.api.previewBuy(quote.symbol, String(this.shares ?? '').trim()),
      (preview) => this.preview.set(preview));
  }

  protected confirm(): void {
    const preview = this.preview();
    if (!preview) {
      return;
    }
    this.run(this.api.buy(preview.quote.symbol, preview.shares), (purchase) => {
      this.notices.show(purchase.message);
      this.auth.refreshUser();
      this.router.navigate(['/']);
    });
  }

  protected startOver(): void {
    this.quote.set(null);
    this.preview.set(null);
    this.error.set(null);
    this.symbol.set('');
  }

  private run<T>(call: Observable<T>, done: (value: T) => void,
                 onBadTicker?: () => void): void {
    this.busy.set(true);
    this.error.set(null);
    call.subscribe({
      next: (value) => {
        this.busy.set(false);
        done(value);
      },
      error: (err) => {
        this.busy.set(false);
        const message = errorMessage(err);
        if (onBadTicker && message.startsWith('No market data found')) {
          onBadTicker();
          return;
        }
        this.error.set(message);
      },
    });
  }
}
