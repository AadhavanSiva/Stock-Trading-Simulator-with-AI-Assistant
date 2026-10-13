import { inject } from '@angular/core';
import { CanActivateFn, CanMatchFn, Router } from '@angular/router';
import { map } from 'rxjs';

import { AuthService } from './auth.service';

/** Pages that need an account. Signed-out readers go to sign in, and come
 *  back to where they were headed afterwards. */
export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.isSignedIn().pipe(
    map((signedIn) => signedIn || router.createUrlTree(['/login'], {
      queryParams: { next: state.url },
    })),
  );
};

/** The sign-in page, for readers who are not signed in yet. */
export const signedOutGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.isSignedIn().pipe(map((signedIn) => !signedIn || router.createUrlTree(['/'])));
};

/** Lets one path show different pages: "/" is the landing page signed
 *  out and the portfolio signed in. */
export const signedOutMatch: CanMatchFn = () =>
  inject(AuthService).isSignedIn().pipe(map((signedIn) => !signedIn));

/** Only same-site paths may be used as a post-sign-in destination; anything
 *  else ("https://evil.example", "//evil.example") is an open redirect. */
export function safeNext(target: string | null | undefined): string {
  if (!target || !target.startsWith('/') || target.startsWith('//') || target.includes('\\')) {
    return '/';
  }
  return target;
}
