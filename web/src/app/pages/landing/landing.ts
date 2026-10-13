import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService } from '../../core/api.service';

/** The front door for someone who is not signed in. */
@Component({
  selector: 'app-landing',
  imports: [RouterLink],
  templateUrl: './landing.html',
})
export class Landing implements OnInit {
  private readonly api = inject(ApiService);

  /** "$50,000", or a phrase that still reads if the API is unreachable. */
  protected readonly startingCash = signal('a balance');

  ngOnInit(): void {
    this.api.authConfig().subscribe({
      next: (config) => this.startingCash.set(wholeDollars(config.starting_cash)),
      // The page is copy; it does not need the figure to be useful.
      error: () => undefined,
    });
  }
}

export function wholeDollars(amount: string): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: 0,
  }).format(Number(amount));
}
