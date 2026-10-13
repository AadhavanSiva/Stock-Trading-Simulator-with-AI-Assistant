import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './auth.service';
import { NoticeService } from './notice.service';

/** Attach the access token to API calls, and send the reader to sign in
 *  again when the API says it has expired.
 *
 *  Only same-origin /api requests get the token: it must never be sent to
 *  another host, however a URL ends up in an HttpClient call. */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const notices = inject(NoticeService);

  if (!request.url.startsWith('/api/')) {
    return next(request);
  }

  const token = auth.token();
  const authed = token
    ? request.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
    : request;

  return next(authed).pipe(
    catchError((error: unknown) => {
      if (error instanceof HttpErrorResponse && error.status === 401
          && !request.url.startsWith('/api/auth/')) {
        auth.clear();
        notices.show(error.error?.detail ?? 'Please sign in again.', 'notice');
        router.navigate(['/login'], { queryParams: { next: router.url } });
      }
      return throwError(() => error);
    }),
  );
};
