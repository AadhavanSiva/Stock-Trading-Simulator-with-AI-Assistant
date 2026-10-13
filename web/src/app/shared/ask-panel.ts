import { HttpErrorResponse } from '@angular/common/http';
import {
  Component, ElementRef, OnDestroy, computed, inject, signal, viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { NavigationEnd, Router } from '@angular/router';
import { Subscription, filter } from 'rxjs';

import { ApiService } from '../core/api.service';
import { ASK_TRANSCRIPT_KEY, AuthService } from '../core/auth.service';
import { AssistantAnswer, AssistantKind, AssistantSource, AssistantStatus } from '../core/models';

/** One answered question, as kept for the rest of the tab's life. */
interface Turn {
  question: string;
  answer: string;
  sources: AssistantSource[];
  suggestions: string;
  searches: string[];
  notice: string;
}

interface Failure {
  kind: AssistantKind;
  question: string;
  message: string;
  /** When a "busy" answer may be retried, as a clock time in ms. */
  retryAt: number | null;
}

type Entry =
  | { type: 'answer'; turn: Turn; frame: SafeHtml | null }
  | { type: 'question'; question: string }
  | { type: 'failure'; failure: Failure };

const KEPT_TURNS = 10;
const EARLIER_TURNS = 3;

/* A failure the reader can act on: what happened, and a way forward. */
const TITLES: Partial<Record<AssistantKind, string>> = {
  invalid: 'Check your question',
  busy: 'Ask needs a short break',
  unavailable: 'Ask is temporarily unavailable',
  disabled: 'Ask is switched off',
  refused: "Ask can't help with that one",
  failed: "Ask couldn't answer that",
  network: "Couldn't reach the app",
  aborted: 'Stopped',
};

/** Ask, the research assistant, as a panel beside whatever page is open.
 *
 *  Model output is only ever bound as text. The one piece of outside HTML
 *  is the search provider's suggestions, which its terms require to be
 *  shown unmodified: it goes into a sandboxed iframe with no scripts,
 *  where its CSS cannot touch this page. */
@Component({
  selector: 'app-ask-panel',
  imports: [FormsModule],
  templateUrl: './ask-panel.html',
  host: { '(document:keydown.escape)': 'close()' },
})
export class AskPanel implements OnDestroy {
  private readonly api = inject(ApiService);
  private readonly auth = inject(AuthService);
  private readonly sanitizer = inject(DomSanitizer);

  private readonly field = viewChild<ElementRef<HTMLTextAreaElement>>('field');
  private readonly log = viewChild<ElementRef<HTMLElement>>('log');
  private readonly toggle = viewChild<ElementRef<HTMLElement>>('toggle');

  protected readonly status = signal<AssistantStatus | null>(null);
  protected readonly open = signal(false);
  protected readonly entries = signal<Entry[]>([]);
  protected readonly busy = signal(false);
  /** Seconds waited so far, or null when nothing is pending. */
  protected readonly waited = signal<number | null>(null);
  protected readonly now = signal(Date.now());
  /** The ticker of the stock page being viewed, or '' anywhere else. */
  protected readonly symbol = signal('');
  protected question = '';

  private transcript: Turn[] = [];
  private pending: Subscription | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  protected readonly maxChars = computed(() => this.status()?.max_question_chars ?? 1000);
  protected readonly starters = computed(() => this.symbol()
    ? ["What has moved this stock's price over the past year?",
       'What does this company do, and how does it make money?',
       'How much has the price swung compared with what I paid?']
    : ['How spread out is my portfolio?',
       'What does average cost mean?',
       'Why does cash plus investments matter?']);

  /* The wait, described honestly by elapsed time. The app cannot see
   * which step the model is on, so it never claims one. */
  protected readonly waitingText = computed(() => {
    const seconds = this.waited() ?? 0;
    if (seconds < 5) {
      return 'Researching…';
    }
    if (seconds < 20) {
      return this.status()?.research
        ? 'Searching the web and reading sources. This usually takes 10 to 20 seconds.'
        : 'Reading your account and stored prices…';
    }
    return seconds < 45
      ? 'Still working. Questions about recent news take longer.'
      : 'Taking longer than usual.';
  });

  constructor() {
    const router = inject(Router);
    this.symbol.set(symbolIn(router.url));
    router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      takeUntilDestroyed(),
    ).subscribe((event) => this.symbol.set(symbolIn(event.urlAfterRedirects)));

    this.api.assistantStatus().subscribe({
      next: (status) => this.status.set(status),
      // No status means no panel; the rest of the app is unaffected.
      error: () => undefined,
    });

    // A restored answer keeps its sources and search suggestions: the
    // terms require the suggestions to accompany a searched answer
    // whenever it is shown, not just the first time.
    this.transcript = this.restore();
    this.entries.set(this.transcript.flatMap((turn) => [
      { type: 'question', question: turn.question } as Entry, this.answerEntry(turn),
    ]));
  }

  ngOnDestroy(): void {
    this.stopWaiting();
  }

  protected show(): void {
    this.open.set(true);
    setTimeout(() => {
      this.field()?.nativeElement.focus();
      this.scrollDown();
    });
  }

  protected close(): void {
    if (!this.open()) {
      return;
    }
    this.open.set(false);
    setTimeout(() => this.toggle()?.nativeElement.focus());
  }

  /** Enter sends; Shift+Enter makes a new line. */
  protected key(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      this.ask(this.question);
    }
  }

  protected ask(question: string): void {
    question = question.trim();
    if (!question || this.busy()) {
      return;
    }
    if (question.length > this.maxChars()) {
      this.fail(question, 'invalid',
        `That question is too long. Keep it under ${this.maxChars()} characters.`);
      return;
    }

    this.busy.set(true);
    this.question = '';
    this.entries.update((list) => [...list, { type: 'question', question }]);
    this.startWaiting();

    const earlier = this.transcript.slice(-EARLIER_TURNS)
      .map(({ question: q, answer }) => ({ question: q, answer }));
    this.pending = this.api.ask(question, this.symbol(), earlier).subscribe({
      next: (data) => {
        this.finish(data.research);
        this.answered(question, data);
      },
      error: (err: unknown) => {
        const body = err instanceof HttpErrorResponse ? err.error as Partial<AssistantAnswer> : null;
        this.finish(body?.research);
        if (body && typeof body.kind === 'string') {
          if (body.kind === 'invalid') {
            this.question = question;
          }
          this.fail(question, body.kind, body.message || 'No answer this time. Try again.',
            body.retry_after ?? null);
        } else if (err instanceof HttpErrorResponse && err.status === 401) {
          // The interceptor is already sending the reader to sign in.
        } else {
          this.question = question;
          this.fail(question, 'network', 'Check the server is still running, then try again.');
        }
      },
    });
  }

  protected cancel(): void {
    const asked = this.lastQuestion();
    // Unsubscribing aborts the request.
    this.pending?.unsubscribe();
    this.finish();
    this.question = asked;
    this.fail(asked, 'aborted',
      "The question was cancelled. It's back in the box if you want to send it again.");
  }

  protected copy(answer: string, button: HTMLButtonElement): void {
    navigator.clipboard?.writeText(answer).then(() => {
      button.textContent = 'Copied';
      setTimeout(() => (button.textContent = 'Copy'), 2000);
    }, () => undefined);
  }

  protected title(kind: AssistantKind): string {
    return TITLES[kind] ?? TITLES.failed!;
  }

  protected canRetry(kind: AssistantKind): boolean {
    return kind === 'failed' || kind === 'network' || kind === 'aborted';
  }

  /** "Try again in 1 min 5 s.", counting down, for a rate-limited answer. */
  protected countdown(failure: Failure): string {
    if (failure.retryAt === null) {
      return '';
    }
    const left = Math.ceil((failure.retryAt - this.now()) / 1000);
    if (left <= 0) {
      return 'You can ask again now.';
    }
    const minutes = Math.floor(left / 60);
    return `Try again in ${minutes ? minutes + ' min ' : ''}${left % 60} s.`;
  }

  protected paragraphs(text: string): string[] {
    return text.split(/\n\s*\n/).map((chunk) => chunk.trim()).filter(Boolean);
  }

  private answered(question: string, data: AssistantAnswer): void {
    const turn: Turn = {
      question,
      answer: data.answer,
      // Only http(s) addresses are ever made into links.
      sources: (data.sources ?? []).filter((src) => /^https?:\/\//i.test(src.uri ?? '')),
      suggestions: data.search_suggestions ?? '',
      searches: data.searches ?? [],
      notice: data.notice ?? '',
    };
    this.transcript = [...this.transcript, turn].slice(-KEPT_TURNS);
    this.save();
    this.entries.update((list) => [...list, this.answerEntry(turn)]);
    this.scrollDown();
  }

  private answerEntry(turn: Turn): Entry {
    // A restored notice is shown in today's wording, not whatever was
    // saved with it.
    const notice = turn.notice ? (this.status()?.research_notice || turn.notice) : '';
    return {
      type: 'answer',
      turn: { ...turn, notice },
      // Trusted as HTML only for a sandboxed frame that cannot run script.
      // Angular's own sanitiser would rewrite it, and the provider's terms
      // require it unmodified. <base> opens its links in a new tab.
      frame: turn.suggestions
        ? this.sanitizer.bypassSecurityTrustHtml(
            '<!doctype html><html><head><meta charset="utf-8"><base target="_blank"></head>'
            + `<body style="margin:0">${turn.suggestions}</body></html>`)
        : null,
    };
  }

  private fail(question: string, kind: AssistantKind, message: string,
               retryAfter: number | null = null): void {
    if (kind === 'disabled') {
      this.status.update((status) => status && { ...status, enabled: false });
    }
    const retryAt = kind === 'busy' && retryAfter ? Date.now() + retryAfter * 1000 : null;
    if (retryAt !== null) {
      this.tickUntil(retryAt);
    }
    this.entries.update((list) => [
      ...list, { type: 'failure', failure: { kind, question, message, retryAt } },
    ]);
    this.scrollDown();
  }

  private startWaiting(): void {
    const started = Date.now();
    this.waited.set(0);
    this.timer = setInterval(
      () => this.waited.set(Math.floor((Date.now() - started) / 1000)), 500);
    this.scrollDown();
  }

  private stopWaiting(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.waited.set(null);
  }

  private finish(research?: boolean): void {
    this.stopWaiting();
    this.pending = null;
    this.busy.set(false);
    if (typeof research === 'boolean') {
      this.status.update((status) => status && { ...status, research });
    }
    setTimeout(() => this.open() && this.field()?.nativeElement.focus());
  }

  private tickUntil(when: number): void {
    const tick = setInterval(() => {
      this.now.set(Date.now());
      if (Date.now() >= when) {
        clearInterval(tick);
      }
    }, 1000);
  }

  private lastQuestion(): string {
    const asked = this.entries().filter((entry) => entry.type === 'question');
    return asked.length ? asked[asked.length - 1].question : '';
  }

  private scrollDown(): void {
    setTimeout(() => {
      const log = this.log()?.nativeElement;
      if (log) {
        log.scrollTop = log.scrollHeight;
      }
    });
  }

  /* Kept per tab and per account: closing the tab or signing out ends it. */
  private restore(): Turn[] {
    try {
      const saved = JSON.parse(sessionStorage.getItem(ASK_TRANSCRIPT_KEY) ?? 'null');
      if (saved?.userId === this.auth.user()?.id && Array.isArray(saved.turns)) {
        return saved.turns.slice(-KEPT_TURNS);
      }
    } catch {
      // Unreadable or unavailable storage: start with an empty panel.
    }
    return [];
  }

  private save(): void {
    try {
      sessionStorage.setItem(ASK_TRANSCRIPT_KEY, JSON.stringify({
        userId: this.auth.user()?.id, turns: this.transcript,
      }));
    } catch {
      // Private mode or full: the panel still works.
    }
  }
}

/** "/stock/aapl?range=1y" -> "AAPL" */
export function symbolIn(url: string): string {
  const match = /^\/stock\/([^/?#]+)/.exec(url);
  return match ? decodeURIComponent(match[1]).toUpperCase() : '';
}
