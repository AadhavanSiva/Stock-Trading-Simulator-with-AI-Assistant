import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter, withComponentInputBinding } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { routes } from './app.routes';
import { AuthService } from './core/auth.service';

describe('routes', () => {
  let backend: HttpTestingController;
  let harness: RouterTestingHarness;
  const text = () => harness.routeNativeElement!.textContent as string;

  beforeEach(async () => {
    localStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideRouter(routes, withComponentInputBinding()),
                  provideHttpClient(), provideHttpClientTesting()],
    });
    backend = TestBed.inject(HttpTestingController);
    harness = await RouterTestingHarness.create();
  });

  afterEach(() => localStorage.clear());

  it('shows the landing page at / when signed out', async () => {
    await harness.navigateByUrl('/');
    expect(TestBed.inject(Router).url).toBe('/');
    expect(text()).toContain('Learn how investing works');
  });

  it('shows the portfolio at / when signed in', async () => {
    TestBed.inject(AuthService).user.set(
      { id: 1, email: 'a@b.c', display_name: 'A', cash: '1', starting_cash: '1' });
    await harness.navigateByUrl('/');
    expect(text()).toContain('Portfolio');
    expect(text()).not.toContain('Learn how investing works');
    backend.expectOne('/api/portfolio');
  });

  it('sends a signed-out reader from an account page to log in, remembering where', async () => {
    await harness.navigateByUrl('/watchlist');
    expect(TestBed.inject(Router).url).toBe('/login?next=%2Fwatchlist');
  });

  it('lets the policies be read signed out', async () => {
    for (const [path, heading] of [['/terms', 'Terms of Service'], ['/privacy', 'Privacy Policy'],
                                   ['/disclaimer', 'Disclaimer']]) {
      await harness.navigateByUrl(path);
      expect(text()).toContain(heading);
      expect(text()).toContain('Draft — not a finished legal document.');
    }
  });

  it('frames /signup for a newcomer, with the terms stated before the account exists', async () => {
    await harness.navigateByUrl('/signup');
    expect(text()).toContain('Open an account');
    expect(text()).toContain('By opening an account you agree');
    await harness.navigateByUrl('/login');
    expect(text()).toContain('Log in');
    expect(text()).not.toContain('By opening an account you agree');
  });
});
