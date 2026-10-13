import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, catchError, map, of, tap } from 'rxjs';

import { ApiService } from './api.service';
import { TokenResponse, User } from './models';

const STORAGE_KEY = 'portfolio-tracker.session';
export const ASK_TRANSCRIPT_KEY = 'portfolio-tracker.ask';

interface StoredSession {
  token: string;
  expiresAt: number;
}

/** Who is signed in, and the access token that proves it.
 *
 *  The token lives in localStorage so a reload keeps you signed in. That
 *  makes it readable by any script on the page, which is the trade-off of
 *  bearer tokens: it is why the app loads no third-party script except
 *  Google's sign-in, and why the token is short-lived. */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  private readonly session = signal<StoredSession | null>(readSession());
  readonly user = signal<User | null>(null);
  readonly signedIn = computed(() => this.session() !== null);

  token(): string | null {
    const session = this.session();
    if (session && session.expiresAt <= Date.now()) {
      this.clear();
      return null;
    }
    return session?.token ?? null;
  }

  /** Store a fresh sign-in from the API. */
  accept(response: TokenResponse): void {
    const session = {
      token: response.access_token,
      expiresAt: Date.now() + response.expires_in * 1000,
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    } catch {
      // Storage can be unavailable (private windows, blocked site data).
      // The sign-in still works for this tab; it just won't survive a reload.
    }
    this.session.set(session);
    this.user.set(response.user);
  }

  /** Load the signed-in account, if any. Resolves to null when signed out
   *  or when the stored token no longer works. */
  loadUser(): Observable<User | null> {
    if (!this.token()) {
      return of(null);
    }
    return this.api.me().pipe(
      tap((user) => this.user.set(user)),
      catchError(() => {
        this.clear();
        return of(null);
      }),
    );
  }

  /** Re-read cash and name after something changes them, like a trade. */
  refreshUser(): void {
    this.loadUser().subscribe();
  }

  signOut(): void {
    this.clear();
    this.router.navigate(['/login']);
  }

  clear(): void {
    try {
      localStorage.removeItem(STORAGE_KEY);
      // The Ask conversation belongs to whoever was signed in.
      sessionStorage.removeItem(ASK_TRANSCRIPT_KEY);
    } catch {
      // Nothing stored to remove.
    }
    this.session.set(null);
    this.user.set(null);
  }

  isSignedIn(): Observable<boolean> {
    return this.user() ? of(true) : this.loadUser().pipe(map((user) => user !== null));
  }
}

function readSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as StoredSession;
    if (typeof parsed.token !== 'string' || typeof parsed.expiresAt !== 'number'
        || parsed.expiresAt <= Date.now()) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}
