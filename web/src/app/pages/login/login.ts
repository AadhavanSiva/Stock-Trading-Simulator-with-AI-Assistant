import {
  AfterViewInit, Component, ElementRef, NgZone, OnDestroy, inject, input, signal, viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { safeNext } from '../../core/auth.guard';
import { AuthConfig, TokenResponse } from '../../core/models';
import { NoticeService } from '../../core/notice.service';
import { MoneyPipe } from '../../shared/format';

const GOOGLE_SCRIPT = 'https://accounts.google.com/gsi/client';

// The parts of Google Identity Services this page uses.
interface GoogleIdentity {
  accounts: {
    id: {
      initialize(options: { client_id: string; callback: (r: { credential: string }) => void }): void;
      renderButton(element: HTMLElement, options: Record<string, unknown>): void;
      cancel(): void;
    };
  };
}

declare global {
  interface Window { google?: GoogleIdentity; }
}

@Component({
  selector: 'app-login',
  imports: [FormsModule, RouterLink, MoneyPipe],
  templateUrl: './login.html',
})
export class Login implements AfterViewInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly notices = inject(NoticeService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  private readonly zone = inject(NgZone);

  private readonly googleButton = viewChild<ElementRef<HTMLElement>>('googleButton');

  /** From the route's data: the same sign-in, framed for a newcomer. */
  readonly mode = input<'login' | 'signup'>('login');

  protected readonly config = signal<AuthConfig | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly busy = signal(false);
  protected readonly origin = location.origin;
  protected email = '';

  ngAfterViewInit(): void {
    this.api.authConfig().subscribe({
      next: (config) => {
        this.config.set(config);
        if (config.google_client_id) {
          // Rendered after the @if that holds the button's container.
          setTimeout(() => this.showGoogleButton(config.google_client_id!));
        }
      },
      error: (err) => this.error.set(errorMessage(err)),
    });
  }

  ngOnDestroy(): void {
    window.google?.accounts.id.cancel();
  }

  protected signInLocally(): void {
    const email = this.email.trim();
    if (!email) {
      this.error.set('Enter an email address to sign in with.');
      return;
    }
    this.busy.set(true);
    this.api.signInLocally(email).subscribe({
      next: (response) => this.finish(response),
      error: (err) => {
        this.busy.set(false);
        this.error.set(errorMessage(err));
      },
    });
  }

  private showGoogleButton(clientId: string): void {
    loadScript(GOOGLE_SCRIPT).then(() => {
      const google = window.google;
      const container = this.googleButton()?.nativeElement;
      if (!google || !container) {
        return;
      }
      google.accounts.id.initialize({
        client_id: clientId,
        // Google calls back outside Angular's zone.
        callback: ({ credential }) => this.zone.run(() => this.exchange(credential)),
      });
      google.accounts.id.renderButton(container, {
        theme: 'outline', size: 'large', shape: 'rectangular',
        text: this.mode() === 'signup' ? 'signup_with' : 'continue_with',
      });
    }).catch(() => this.error.set(
      'Could not load Google sign-in. Check your connection and reload the page.'));
  }

  private exchange(credential: string): void {
    this.busy.set(true);
    this.error.set(null);
    this.api.signInWithGoogle(credential).subscribe({
      next: (response) => this.finish(response),
      error: (err) => {
        this.busy.set(false);
        this.error.set(errorMessage(err));
      },
    });
  }

  private finish(response: TokenResponse): void {
    this.auth.accept(response);
    const cash = new Intl.NumberFormat('en-US', {
      style: 'currency', currency: 'USD', maximumFractionDigits: 0,
    }).format(Number(response.user.starting_cash));
    this.notices.show(response.new_account
      ? `Welcome. Your account is ready with ${cash} of practice money.`
      : `Signed in as ${response.user.email}.`);
    this.router.navigateByUrl(safeNext(this.route.snapshot.queryParamMap.get('next')));
  }
}

function loadScript(src: string): Promise<void> {
  const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
  if (existing && window.google) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const script = existing ?? document.createElement('script');
    script.addEventListener('load', () => resolve(), { once: true });
    script.addEventListener('error', () => reject(), { once: true });
    if (!existing) {
      script.src = src;
      script.async = true;
      document.head.appendChild(script);
    }
  });
}
