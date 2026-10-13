import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { AuthService } from '../../core/auth.service';
import { Account as AccountData } from '../../core/models';
import { Account } from './account';

const ACCOUNT: AccountData = {
  email: 'a@b.c', display_name: 'A', cash: '50000.00', starting_cash: '50000',
  trade_count: 3, delete_confirmation: 'DELETE', leaderboard_opt_in: false,
  leaderboard_name: null, leaderboard_name_max: 24, standing: null,
};

describe('Account', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<Account>;
  let auth: AuthService;
  let router: Router;
  const element = () => fixture.nativeElement as HTMLElement;

  function type(selector: string, value: string): void {
    const input = element().querySelector(selector) as HTMLInputElement;
    input.value = value;
    input.dispatchEvent(new Event('input'));
  }

  function submit(index: number): void {
    element().querySelectorAll('form')[index].dispatchEvent(new Event('submit'));
  }

  beforeEach(async () => {
    localStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    backend = TestBed.inject(HttpTestingController);
    auth = TestBed.inject(AuthService);
    router = TestBed.inject(Router);
    fixture = TestBed.createComponent(Account);
    fixture.detectChanges();
    backend.expectOne('/api/account').flush(ACCOUNT);
    fixture.detectChanges();
    await fixture.whenStable();
  });

  afterEach(() => {
    backend.verify();
    localStorage.clear();
  });

  it('joins the leaderboard under the chosen name', () => {
    type('#leaderboard-name', 'Racer');
    (element().querySelector('input[type="checkbox"]') as HTMLInputElement).click();
    submit(0);

    const call = backend.expectOne('/api/account/leaderboard');
    expect(call.request.body).toEqual({ opt_in: true, name: 'Racer' });
    call.flush({ opt_in: true, name: 'Racer', standing: null,
                 message: 'You are on the leaderboard now.' });
    fixture.detectChanges();
    expect(element().textContent).toContain('You are on the leaderboard now.');
    expect(element().textContent).toContain('You are listed');
  });

  it('shows why a name was refused', () => {
    submit(0);
    backend.expectOne('/api/account/leaderboard')
      .flush({ detail: 'Choose a name to appear under.' }, { status: 400, statusText: 'Bad Request' });
    fixture.detectChanges();
    expect(element().textContent).toContain('Choose a name to appear under.');
  });

  it('stays signed in when the delete confirmation is wrong', () => {
    auth.accept({ access_token: 't', token_type: 'bearer', expires_in: 3600, new_account: false,
                  user: { id: 1, email: 'a@b.c', display_name: 'A', cash: '1', starting_cash: '1' } });
    type('#confirmation', 'nope');
    submit(1);
    backend.expectOne('/api/account/delete')
      .flush({ detail: 'Nothing has been deleted.' }, { status: 400, statusText: 'Bad Request' });
    fixture.detectChanges();
    expect(element().textContent).toContain('Nothing has been deleted.');
    expect(auth.signedIn()).toBeTrue();
  });

  it('signs out and says the account is gone once it is deleted', () => {
    auth.accept({ access_token: 't', token_type: 'bearer', expires_in: 3600, new_account: false,
                  user: { id: 1, email: 'a@b.c', display_name: 'A', cash: '1', starting_cash: '1' } });
    const navigate = spyOn(router, 'navigate').and.resolveTo(true);
    type('#confirmation', 'DELETE');
    submit(1);

    const call = backend.expectOne('/api/account/delete');
    expect(call.request.body).toEqual({ confirmation: 'DELETE' });
    call.flush({ email: 'a@b.c' });
    expect(auth.signedIn()).toBeFalse();
    expect(navigate).toHaveBeenCalledWith(
      ['/account/deleted'], { state: { email: 'a@b.c', startingCash: '50000' } });
  });
});
