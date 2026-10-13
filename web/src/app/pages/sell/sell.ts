import { UpperCasePipe } from '@angular/common';
import { Component, OnInit, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { SellPreview, SellQuote } from '../../core/models';
import { NoticeService } from '../../core/notice.service';
import { Delta } from '../../shared/delta';
import { FORMAT_PIPES, trimShares } from '../../shared/format';

/** Sell some or all of one position: choose how many, then confirm.
 *  Only the confirm step writes, and it prices the sale afresh. */
@Component({
  selector: 'app-sell',
  imports: [FormsModule, RouterLink, UpperCasePipe, Delta, ...FORMAT_PIPES],
  templateUrl: './sell.html',
})
export class Sell implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly notices = inject(NoticeService);
  private readonly router = inject(Router);

  /** From the route: /sell/:symbol */
  readonly symbol = input.required<string>();

  protected readonly quote = signal<SellQuote | null>(null);
  protected readonly preview = signal<SellPreview | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected shares = '';

  ngOnInit(): void {
    this.api.sellQuote(this.symbol()).subscribe({
      next: (quote) => this.quote.set(quote),
      error: (err) => {
        this.notices.show(errorMessage(err), 'error');
        this.router.navigate(['/sell']);
      },
    });
  }

  protected sellAll(): void {
    const quote = this.quote();
    if (quote) {
      // The truncated maximum, never the raw holding: it is always safe to submit.
      this.shares = trimShares(quote.maximum);
    }
  }

  protected review(): void {
    const quote = this.quote();
    if (!quote) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.api.previewSell(quote.symbol, String(this.shares ?? '').trim()).subscribe({
      next: (preview) => {
        this.busy.set(false);
        this.preview.set(preview);
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(errorMessage(err));
      },
    });
  }

  protected confirm(): void {
    const preview = this.preview();
    if (!preview) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.api.sell(preview.symbol, preview.shares).subscribe({
      next: (sale) => {
        this.notices.show(sale.message);
        this.auth.refreshUser();
        this.router.navigate(['/']);
      },
      error: (err) => {
        this.busy.set(false);
        this.error.set(errorMessage(err));
      },
    });
  }
}
