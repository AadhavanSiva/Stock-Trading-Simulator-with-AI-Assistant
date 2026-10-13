import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { Holding } from '../../core/models';
import { Delta } from '../../shared/delta';
import { FORMAT_PIPES } from '../../shared/format';

/** Choose which position to sell from. */
@Component({
  selector: 'app-sell-list',
  imports: [RouterLink, Delta, ...FORMAT_PIPES],
  template: `
    <header class="page-head"><h1>Sell</h1></header>
    @if (error()) {
      <p class="form-error" role="alert">{{ error() }}</p>
    }
    @if (holdings(); as list) {
      <div class="panel">
        @if (list.length) {
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">Stock</th>
                  <th scope="col" class="num">Shares</th>
                  <th scope="col" class="num">Value</th>
                  <th scope="col" class="num">Gain / loss</th>
                  <th scope="col"><span class="visually-hidden">Action</span></th>
                </tr>
              </thead>
              <tbody>
                @for (h of list; track h.symbol) {
                  <tr>
                    <th scope="row">
                      <span class="symbol">{{ h.symbol }}</span>
                      <span class="company">{{ h.company_name }}</span>
                    </th>
                    <td class="num">{{ h.shares | shares }}</td>
                    <td class="num">{{ h.value | money }}</td>
                    <td class="num"><app-delta [amount]="h.gain_loss" [percent]="h.percent" /></td>
                    <td class="num"><a class="button small" [routerLink]="['/sell', h.symbol]">Sell {{ h.symbol }}</a></td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <p class="empty">You don't own anything to sell. <a routerLink="/buy">Buy a stock</a> first.</p>
        }
      </div>
    } @else if (!error()) {
      <p class="muted">Loading…</p>
    }
  `,
})
export class SellList implements OnInit {
  private readonly api = inject(ApiService);
  protected readonly holdings = signal<Holding[] | null>(null);
  protected readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.api.portfolio().subscribe({
      next: (p) => this.holdings.set(p.holdings),
      error: (err) => this.error.set(errorMessage(err)),
    });
  }
}
