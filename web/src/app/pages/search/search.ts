import { Component, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { SearchResult } from '../../core/models';

/** Find a company by name or ticker. The query lives in the URL, so
 *  results are linkable and the back button works. */
@Component({
  selector: 'app-search',
  imports: [FormsModule, RouterLink],
  template: `
    <header class="page-head"><h1>Find a stock</h1></header>
    <form class="panel narrow search-form" (ngSubmit)="submit()">
      <label for="q">Company or ticker</label>
      <div class="input-row">
        <input id="q" name="q" type="search" [(ngModel)]="text" autocomplete="off" />
        <button type="submit" class="button primary">Search</button>
      </div>
    </form>
    @if (error()) {
      <p class="form-error" role="alert">{{ error() }}</p>
    }
    @if (q()) {
      <div class="panel">
        @if (results(); as list) {
          @if (list.length) {
            <ul class="results">
              @for (r of list; track r.symbol) {
                <li>
                  <a [routerLink]="['/stock', r.symbol]"><strong>{{ r.symbol }}</strong> {{ r.name }}</a>
                  <a class="button small" routerLink="/buy" [queryParams]="{ symbol: r.symbol }">Buy</a>
                </li>
              }
            </ul>
          } @else {
            <p class="empty">No companies match “{{ q() }}”. Try the ticker, or a shorter name.</p>
          }
        } @else {
          <p class="muted">Searching…</p>
        }
      </div>
    }
  `,
})
export class Search {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  /** From the route: /search?q= */
  readonly q = input<string | undefined>();

  protected readonly results = signal<SearchResult[] | null>(null);
  protected readonly error = signal<string | null>(null);
  protected text = '';

  constructor() {
    effect(() => {
      const q = (this.q() ?? '').trim();
      this.text = q;
      this.results.set(null);
      if (q) {
        this.api.search(q, 25).subscribe({
          next: (results) => this.results.set(results),
          error: (err) => this.error.set(errorMessage(err)),
        });
      }
    });
  }

  protected submit(): void {
    this.router.navigate([], { queryParams: { q: this.text.trim() || null } });
  }
}
