import { Component, effect, inject, input, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { TradePage } from '../../core/models';
import { Delta } from '../../shared/delta';
import { FORMAT_PIPES } from '../../shared/format';

/** Every buy and sell, newest first, a page at a time. */
@Component({
  selector: 'app-trades',
  imports: [RouterLink, Delta, ...FORMAT_PIPES],
  templateUrl: './trades.html',
})
export class Trades {
  private readonly api = inject(ApiService);

  /** From the route: /trades?page= */
  readonly page = input<string | undefined>();

  protected readonly data = signal<TradePage | null>(null);
  protected readonly error = signal<string | null>(null);

  constructor() {
    // Re-load whenever the route's page changes; untracked so nothing
    // load() touches can re-run this.
    effect(() => {
      const page = this.page();
      untracked(() => this.load(page));
    });
  }

  protected load(page: string | undefined = this.page()): void {
    this.error.set(null);
    this.api.trades(pageNumber(page)).subscribe({
      next: (data) => this.data.set(data),
      error: (err) => this.error.set(errorMessage(err)),
    });
  }
}

/** A hand-edited ?page= falls back to the first page instead of failing. */
export function pageNumber(value: string | undefined): number {
  const page = Number(value);
  return Number.isInteger(page) && page >= 1 ? page : 1;
}
