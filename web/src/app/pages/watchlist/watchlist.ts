import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Observable } from 'rxjs';

import { ApiService, errorMessage } from '../../core/api.service';
import { Watchlist as WatchlistData } from '../../core/models';
import { Delta } from '../../shared/delta';
import { FORMAT_PIPES } from '../../shared/format';
import { SymbolSearch } from '../../shared/symbol-search';

/** Companies followed without being owned. Nothing here costs anything
 *  or counts toward the account's value. */
@Component({
  selector: 'app-watchlist',
  imports: [RouterLink, Delta, SymbolSearch, ...FORMAT_PIPES],
  templateUrl: './watchlist.html',
})
export class Watchlist implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly data = signal<WatchlistData | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly message = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly symbol = signal('');

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.api.watchlist().subscribe({
      next: (data) => this.data.set(data),
      error: (err) => this.error.set(errorMessage(err)),
    });
  }

  protected follow(symbol: string = this.symbol()): void {
    if (!symbol.trim()) {
      this.error.set('Enter a company or ticker to follow.');
      return;
    }
    this.change(this.api.follow(symbol.trim()), () => this.symbol.set(''));
  }

  protected unfollow(symbol: string): void {
    this.change(this.api.unfollow(symbol));
  }

  protected refresh(): void {
    this.change(this.api.refreshWatchlist());
  }

  /** Run a change, say what it did, and re-read the list. */
  private change(call: Observable<{ message: string }>, done?: () => void): void {
    this.busy.set(true);
    this.error.set(null);
    this.message.set(null);
    call.subscribe({
      next: (result) => {
        this.busy.set(false);
        this.message.set(result.message);
        done?.();
        this.load();
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(errorMessage(err));
      },
    });
  }
}
