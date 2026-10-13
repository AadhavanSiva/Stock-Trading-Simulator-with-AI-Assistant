import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { ASK_TRANSCRIPT_KEY, AuthService } from '../core/auth.service';
import { AssistantAnswer } from '../core/models';
import { AskPanel, symbolIn } from './ask-panel';

const STATUS = { enabled: true, research: true, research_notice: 'No web.', max_question_chars: 1000 };
const ANSWER: AssistantAnswer = {
  ok: true, answer: 'First part.\n\nSecond part.', kind: 'ok', message: '',
  sources: [
    { title: 'Report', uri: 'https://example.com/r', domain: 'example.com' },
    { title: 'Bad', uri: 'javascript:alert(1)', domain: '' },
  ],
  search_suggestions: '<style>.x{color:red}</style><a href="https://google.com/search?q=x">x</a>',
  notice: '', searches: ['apple earnings'], retry_after: null, research: true,
};

describe('AskPanel', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<AskPanel>;
  const element = () => fixture.nativeElement as HTMLElement;

  function start(status = STATUS): void {
    fixture = TestBed.createComponent(AskPanel);
    fixture.detectChanges();
    backend.expectOne('/api/assistant/status').flush(status);
    fixture.detectChanges();
  }

  async function openAndAsk(question: string): Promise<void> {
    if (!element().querySelector('.ask')) {
      (element().querySelector('.ask-toggle') as HTMLButtonElement).click();
      fixture.detectChanges();
      // ngModel registers its control a tick after the form appears.
      await fixture.whenStable();
    }
    const field = element().querySelector('textarea') as HTMLTextAreaElement;
    field.value = question;
    field.dispatchEvent(new Event('input'));
    element().querySelector('form')!.dispatchEvent(new Event('submit'));
    fixture.detectChanges();
  }

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    backend = TestBed.inject(HttpTestingController);
    TestBed.inject(AuthService).user.set(
      { id: 7, email: 'a@b.c', display_name: 'A', cash: '1', starting_cash: '1' });
  });

  afterEach(() => {
    backend.verify();
    sessionStorage.clear();
  });

  it('reads the ticker from a stock page address and nowhere else', () => {
    expect(symbolIn('/stock/aapl?range=1y')).toBe('AAPL');
    expect(symbolIn('/stock/BRK.B')).toBe('BRK.B');
    expect(symbolIn('/')).toBe('');
    expect(symbolIn('/sell/AAPL')).toBe('');
  });

  it('is absent when the assistant is switched off', () => {
    start({ ...STATUS, enabled: false });
    expect(element().querySelector('.ask-toggle')).toBeNull();
  });

  it('shows an answer as text with its sources, and frames the suggestions unmodified', async () => {
    start();
    await openAndAsk('Why did it move?');
    const call = backend.expectOne('/api/assistant');
    expect(call.request.body).toEqual({ question: 'Why did it move?', symbol: '', earlier: [] });
    call.flush(ANSWER);
    fixture.detectChanges();

    const reply = element().querySelector('.turn-answer') as HTMLElement;
    expect(Array.from(reply.querySelectorAll(':scope > p')).slice(0, 2).map((p) => p.textContent))
      .toEqual(['First part.', 'Second part.']);
    // Only the http(s) source becomes a link.
    const links = Array.from(reply.querySelectorAll('.sources a')) as HTMLAnchorElement[];
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://example.com/r']);
    expect(reply.textContent).toContain('apple earnings');

    const frame = reply.querySelector('iframe') as HTMLIFrameElement;
    expect(frame.getAttribute('sandbox')).toBe('allow-popups allow-popups-to-escape-sandbox');
    expect(frame.getAttribute('srcdoc')).toContain(ANSWER.search_suggestions);
  });

  it('never treats model output as HTML', async () => {
    start();
    await openAndAsk('q');
    backend.expectOne('/api/assistant').flush({
      ...ANSWER, answer: '<img src=x onerror=alert(1)>', sources: [], search_suggestions: '', searches: [],
    });
    fixture.detectChanges();
    const reply = element().querySelector('.turn-answer') as HTMLElement;
    expect(reply.querySelector('img')).toBeNull();
    expect(reply.textContent).toContain('<img src=x onerror=alert(1)>');
  });

  it('sends only the words of earlier turns back, and keeps the conversation for the tab', async () => {
    start();
    await openAndAsk('one');
    backend.expectOne('/api/assistant').flush(ANSWER);
    fixture.detectChanges();
    await openAndAsk('two');
    const second = backend.expectOne('/api/assistant');
    expect(second.request.body.earlier).toEqual([{ question: 'one', answer: ANSWER.answer }]);
    second.flush(ANSWER);

    const saved = JSON.parse(sessionStorage.getItem(ASK_TRANSCRIPT_KEY)!);
    expect(saved.userId).toBe(7);
    expect(saved.turns.length).toBe(2);
  });

  it("does not restore another account's conversation", () => {
    sessionStorage.setItem(ASK_TRANSCRIPT_KEY, JSON.stringify({
      userId: 99, turns: [{ question: 'theirs', answer: 'private', sources: [], suggestions: '',
                            searches: [], notice: '' }],
    }));
    start();
    (element().querySelector('.ask-toggle') as HTMLButtonElement).click();
    fixture.detectChanges();
    expect(element().textContent).not.toContain('private');
  });

  it('explains a rate limit and puts an invalid question back in the box', async () => {
    start();
    await openAndAsk('too soon');
    backend.expectOne('/api/assistant').flush(
      { ...ANSWER, ok: false, answer: '', kind: 'busy', message: 'Ask again shortly.', retry_after: 65 },
      { status: 429, statusText: 'Too Many Requests' });
    fixture.detectChanges();
    const failure = element().querySelector('.turn-error') as HTMLElement;
    expect(failure.textContent).toContain('Ask needs a short break');
    expect(failure.textContent).toMatch(/Try again in 1 min \d+ s\./);
    expect(failure.querySelector('button')).toBeNull();

    await openAndAsk('bad');
    backend.expectOne('/api/assistant').flush(
      { ...ANSWER, ok: false, answer: '', kind: 'invalid', message: 'Ask a question first.' },
      { status: 400, statusText: 'Bad Request' });
    fixture.detectChanges();
    expect(fixture.componentInstance['question']).toBe('bad');
  });

  it('offers a retry when the app cannot be reached', async () => {
    start();
    await openAndAsk('hello');
    backend.expectOne('/api/assistant').error(new ProgressEvent('error'));
    fixture.detectChanges();
    const failure = element().querySelector('.turn-error') as HTMLElement;
    expect(failure.textContent).toContain("Couldn't reach the app");
    expect(failure.querySelector('button')!.textContent).toContain('Try again');
  });
});
