import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { Account as AccountData } from '../../core/models';
import { FORMAT_PIPES } from '../../shared/format';

/** Where the account itself is managed: the leaderboard choice, taking
 *  the data out, or ending the account. */
@Component({
  selector: 'app-account',
  imports: [FormsModule, ...FORMAT_PIPES],
  templateUrl: './account.html',
})
export class Account implements OnInit {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly data = signal<AccountData | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);

  protected readonly boardError = signal<string | null>(null);
  protected readonly boardSaved = signal<string | null>(null);
  protected readonly exportError = signal<string | null>(null);
  protected readonly deleteError = signal<string | null>(null);

  protected leaderboardName = '';
  protected optIn = false;
  protected confirmation = '';

  ngOnInit(): void {
    this.api.account().subscribe({
      next: (data) => {
        this.data.set(data);
        this.leaderboardName = data.leaderboard_name ?? '';
        this.optIn = data.leaderboard_opt_in;
      },
      error: (err) => this.error.set(errorMessage(err)),
    });
  }

  protected saveLeaderboard(): void {
    this.busy.set(true);
    this.boardError.set(null);
    this.boardSaved.set(null);
    this.api.setLeaderboard(this.optIn, this.leaderboardName).subscribe({
      next: (result) => {
        this.busy.set(false);
        this.boardSaved.set(result.message);
        this.leaderboardName = result.name ?? '';
        this.optIn = result.opt_in;
        this.data.update((d) => d && {
          ...d, leaderboard_opt_in: result.opt_in, leaderboard_name: result.name,
          standing: result.standing,
        });
      },
      error: (err) => {
        this.busy.set(false);
        this.boardError.set(errorMessage(err));
      },
    });
  }

  protected download(): void {
    this.exportError.set(null);
    this.api.exportAccount().subscribe({
      next: (file) => saveFile(file, `portfolio-tracker-export-${today()}.json`),
      error: (err) => this.exportError.set(errorMessage(err)),
    });
  }

  protected deleteAccount(): void {
    const account = this.data();
    if (!account) {
      return;
    }
    this.busy.set(true);
    this.deleteError.set(null);
    this.api.deleteAccount(this.confirmation).subscribe({
      next: (gone) => {
        // The token names an account that no longer exists.
        this.auth.clear();
        this.router.navigate(['/account/deleted'], {
          state: { email: gone.email, startingCash: account.starting_cash },
        });
      },
      error: (err) => {
        this.busy.set(false);
        this.deleteError.set(errorMessage(err));
      },
    });
  }
}

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

/** Hand the browser a file to save, without leaving the page. */
function saveFile(file: Blob, name: string): void {
  const url = URL.createObjectURL(file);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
