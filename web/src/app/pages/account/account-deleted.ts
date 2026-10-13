import { Component, OnInit, inject } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { MoneyPipe } from '../../shared/format';

/** Shown to the person who just deleted their account. They are signed
 *  out by this point; arriving any other way goes back to the start. */
@Component({
  selector: 'app-account-deleted',
  imports: [RouterLink, MoneyPipe],
  template: `
    @if (email) {
      <section class="panel entry">
        <h1>Your account is gone</h1>
        <p>
          Everything belonging to <strong>{{ email }}</strong> has been deleted:
          the account itself, its cash balance, every position and the whole
          trade history. You have been signed out.
        </p>
        <p class="muted">
          Nothing is kept and nothing can be restored. Signing in with the same
          Google account again opens a new account, starting fresh with
          {{ startingCash | money }} in practice cash.
        </p>
        <a class="button primary" routerLink="/login">Back to the start</a>
      </section>
    }
  `,
})
export class AccountDeleted implements OnInit {
  private readonly router = inject(Router);

  // Passed by the account page as navigation state, so the address is
  // never in the URL.
  protected readonly email: string | null = history.state?.email ?? null;
  protected readonly startingCash: string | null = history.state?.startingCash ?? null;

  ngOnInit(): void {
    if (!this.email) {
      this.router.navigate(['/']);
    }
  }
}
