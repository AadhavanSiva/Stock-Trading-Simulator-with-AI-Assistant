import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { Portfolio } from '../../core/models';
import { Delta } from '../../shared/delta';
import { FORMAT_PIPES } from '../../shared/format';

/** Where the money is: cash, investments, and the two added together.
 *  The same figures as the portfolio page, so it reads the same data. */
@Component({
  selector: 'app-balance',
  imports: [RouterLink, Delta, ...FORMAT_PIPES],
  templateUrl: './balance.html',
})
export class Balance implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly data = signal<Portfolio | null>(null);
  protected readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.error.set(null);
    this.api.portfolio().subscribe({
      next: (data) => this.data.set(data),
      error: (err) => this.error.set(errorMessage(err)),
    });
  }
}
