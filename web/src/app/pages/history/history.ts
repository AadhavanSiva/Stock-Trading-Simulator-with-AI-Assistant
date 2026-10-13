import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { PriceHistory } from '../../core/models';
import { FORMAT_PIPES } from '../../shared/format';

/** Each holding's price today against its recent closes. */
@Component({
  selector: 'app-history',
  imports: [RouterLink, ...FORMAT_PIPES],
  templateUrl: './history.html',
})
export class History implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly data = signal<PriceHistory | null>(null);
  protected readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.error.set(null);
    this.api.history().subscribe({
      next: (data) => this.data.set(data),
      error: (err) => this.error.set(errorMessage(err)),
    });
  }
}
