import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { Portfolio as PortfolioData } from '../../core/models';
import { Delta } from '../../shared/delta';
import { FORMAT_PIPES } from '../../shared/format';
import { PriceChart } from '../../shared/price-chart';

@Component({
  selector: 'app-portfolio',
  imports: [RouterLink, Delta, PriceChart, ...FORMAT_PIPES],
  templateUrl: './portfolio.html',
})
export class Portfolio implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly data = signal<PortfolioData | null>(null);
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
