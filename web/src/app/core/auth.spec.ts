import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { safeNext } from './auth.guard';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';
import { TokenResponse } from './models';
import { NoticeService } from './notice.service';

const SIGN_IN: TokenResponse = {
  access_token: 'token-123',
  token_type: 'bearer',
  expires_in: 3600,
  new_account: false,
  user: { id: 1, email: 'a@b.c', display_name: 'A', cash: '50000', starting_cash: '50000' },
};

describe('auth', () => {
  let http: HttpClient;
  let backend: HttpTestingController;
  let auth: AuthService;

  beforeEach(() => {
    localStorage.clear();
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpClient);
    backend = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(AuthService);
  });

  afterEach(() => {
    backend.verify();
    localStorage.clear();
  });

  it('sends the token to the API once signed in', () => {
    auth.accept(SIGN_IN);
    http.get('/api/portfolio').subscribe();
    const request = backend.expectOne('/api/portfolio');
    expect(request.request.headers.get('Authorization')).toBe('Bearer token-123');
    request.flush({});
  });

  it('never sends the token to another host', () => {
    auth.accept(SIGN_IN);
    http.get('https://example.com/api/portfolio').subscribe();
    const request = backend.expectOne('https://example.com/api/portfolio');
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({});
  });

  it('sends no header when signed out', () => {
    http.get('/api/auth/config').subscribe();
    const request = backend.expectOne('/api/auth/config');
    expect(request.request.headers.has('Authorization')).toBeFalse();
    request.flush({});
  });

  it('signs out and asks to sign in again when the API says 401', () => {
    const router = TestBed.inject(Router);
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);
    auth.accept(SIGN_IN);

    http.get('/api/portfolio').subscribe({ error: () => undefined });
    backend.expectOne('/api/portfolio').flush(
      { detail: 'Your sign-in has expired. Please sign in again.' },
      { status: 401, statusText: 'Unauthorized' });

    expect(auth.token()).toBeNull();
    expect(navigate).toHaveBeenCalledWith(['/login'], jasmine.anything());
    expect(TestBed.inject(NoticeService).notices()[0].text).toContain('expired');
  });

  it('keeps the session across a reload', () => {
    auth.accept(SIGN_IN);
    const stored = JSON.parse(localStorage.getItem('portfolio-tracker.session')!);
    expect(stored.token).toBe('token-123');
  });

  it('forgets an expired session', () => {
    auth.accept({ ...SIGN_IN, expires_in: -1 });
    expect(auth.token()).toBeNull();
  });

  it('only redirects to paths on this site after sign-in', () => {
    expect(safeNext('/stock/AAPL?range=1y')).toBe('/stock/AAPL?range=1y');
    expect(safeNext('https://evil.example')).toBe('/');
    expect(safeNext('//evil.example')).toBe('/');
    expect(safeNext('/\\evil.example')).toBe('/');
    expect(safeNext(null)).toBe('/');
  });
});
