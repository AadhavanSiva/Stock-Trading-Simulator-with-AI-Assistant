import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Observable } from 'rxjs';

import { ApiService, errorMessage } from '../../core/api.service';
import { ActionReport } from '../../core/models';

type Job = 'prices' | 'history';

/** Fetching prices on request. Neither job buys or sells anything. */
@Component({
  selector: 'app-actions',
  imports: [RouterLink],
  template: `
    <header class="page-head">
      <div>
        <h1>Update data</h1>
        <p class="lede">
          Prices are saved when you fetch them, not streamed continuously. Use
          these when you want today's numbers. Neither one buys or sells anything.
        </p>
      </div>
    </header>

    @if (error()) {
      <p class="form-error" role="alert">{{ error() }}</p>
    }
    @if (report(); as r) {
      <p class="notice" [class]="r.kind" role="status">{{ r.message }}</p>
    }

    <section class="panel">
      <div class="job">
        <div>
          <h2>Today's prices</h2>
          <p class="muted">
            Looks up the current market price for everything you own and updates
            your portfolio. A second or two per holding.
          </p>
        </div>
        <button type="button" class="button primary" (click)="run('prices')" [disabled]="running() !== null">
          {{ running() === 'prices' ? 'Fetching prices…' : 'Fetch prices' }}
        </button>
      </div>
      <div class="job">
        <div>
          <h2>Past prices</h2>
          <p class="muted">
            Downloads six months of daily prices for each holding, which is what
            the <a routerLink="/history">history</a> page reads. Safe to run more
            than once — days already stored are skipped rather than duplicated.
          </p>
        </div>
        <button type="button" class="button" (click)="run('history')" [disabled]="running() !== null">
          {{ running() === 'history' ? 'Downloading…' : 'Fetch past prices' }}
        </button>
      </div>
    </section>

    @if (report(); as r) {
      @if (r.outcomes.length) {
        <section class="panel">
          <h2>{{ r.title }}</h2>
          <p class="muted">What happened for each holding.</p>
          <ul class="plain-list outcomes">
            @for (o of r.outcomes; track o.symbol) {
              <li>
                <span class="symbol">{{ o.symbol }}</span>
                <span [class.outcome-bad]="!o.ok">
                  @if (!o.ok) { <strong>Failed —</strong> } {{ o.message }}
                </span>
              </li>
            }
          </ul>
        </section>
      } @else {
        <p class="muted">
          There is nothing to update until you own something.
          <a routerLink="/buy">Buy your first shares</a> to begin.
        </p>
      }
    }
  `,
})
export class Actions {
  private readonly api = inject(ApiService);

  protected readonly running = signal<Job | null>(null);
  protected readonly report = signal<ActionReport | null>(null);
  protected readonly error = signal<string | null>(null);

  protected run(job: Job): void {
    const call: Observable<ActionReport> =
      job === 'prices' ? this.api.refreshPrices() : this.api.loadHistory();
    this.running.set(job);
    this.error.set(null);
    this.report.set(null);
    call.subscribe({
      next: (report) => {
        this.running.set(null);
        this.report.set(report);
      },
      error: (err) => {
        this.running.set(null);
        this.error.set(errorMessage(err));
      },
    });
  }
}
