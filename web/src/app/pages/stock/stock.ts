import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { StockDetail } from '../../core/models';
import { Delta } from '../../shared/delta';
import { FORMAT_PIPES } from '../../shared/format';
import { PriceChart } from '../../shared/price-chart';

/** One company: its price now, a chart of the chosen range, and your
 *  position in it if you have one. */
@Component({
  selector: 'app-stock',
  imports: [RouterLink, Delta, PriceChart, ...FORMAT_PIPES],
  templateUrl: './stock.html',
})
export class Stock {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  /** From the route: /stock/:symbol?range= */
  readonly symbol = input.required<string>();
  readonly range = input<string | undefined>();

  protected readonly detail = signal<StockDetail | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly fetching = signal(false);
  protected readonly fetched = signal<string | null>(null);
  protected readonly watchBusy = signal(false);

  protected readonly dayChange = computed(() => {
    const quote = this.detail()?.quote;
    if (!quote?.previous_close) {
      return null;
    }
    // Display only: the difference of two decimal strings, shown rounded.
    const change = Number(quote.price) - Number(quote.previous_close);
    return {
      amount: change.toFixed(2),
      percent: ((change / Number(quote.previous_close)) * 100).toFixed(2),
    };
  });

  constructor() {
    // Re-load whenever the route's symbol or range changes. load() reads
    // and then sets `detail`; untracked keeps that from re-running this.
    effect(() => {
      const symbol = this.symbol();
      const range = this.range();
      untracked(() => this.load(symbol, range));
    });
  }

  protected choose(range: string): void {
    this.router.navigate([], { queryParams: { range }, replaceUrl: true });
  }

  protected fetch(): void {
    const detail = this.detail();
    if (!detail) {
      return;
    }
    this.fetching.set(true);
    this.api.fetchChart(detail.symbol, detail.range).subscribe({
      next: (result) => {
        this.fetching.set(false);
        this.fetched.set(result.message);
        this.load(detail.symbol, detail.range);
      },
      error: (err) => {
        this.fetching.set(false);
        this.error.set(errorMessage(err));
      },
    });
  }

  /** Follow or unfollow. Not a trade, so it changes nothing but the flag. */
  protected toggleWatch(): void {
    const detail = this.detail();
    if (!detail) {
      return;
    }
    this.watchBusy.set(true);
    const call = detail.watching ? this.api.unfollow(detail.symbol) : this.api.follow(detail.symbol);
    call.subscribe({
      next: (result) => {
        this.watchBusy.set(false);
        this.fetched.set(result.message);
        this.detail.update((d) => d && { ...d, watching: !detail.watching });
      },
      error: (err) => {
        this.watchBusy.set(false);
        this.error.set(errorMessage(err));
      },
    });
  }

  private load(symbol: string, range: string | undefined): void {
    this.error.set(null);
    if (this.detail()?.symbol !== symbol.toUpperCase()) {
      this.fetched.set(null);
    }
    this.api.stock(symbol, range).subscribe({
      next: (detail) => this.detail.set(detail),
      error: (err) => {
        this.detail.set(null);
        this.error.set(errorMessage(err));
      },
    });
  }
}
