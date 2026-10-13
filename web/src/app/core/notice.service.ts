import { Injectable, inject, signal } from '@angular/core';
import { NavigationEnd, NavigationStart, Router } from '@angular/router';

export type NoticeKind = 'success' | 'error' | 'notice';

export interface Notice {
  id: number;
  kind: NoticeKind;
  text: string;
  raisedAt: number;
}

/** Messages that outlive the page that raised them, like "Bought 3
 *  shares" shown on the portfolio after the buy page navigates there.
 *
 *  A notice lasts until the reader leaves the first page it was shown on:
 *  on each navigation, notices raised before the previous page finished
 *  loading have been seen, and are dropped. */
@Injectable({ providedIn: 'root' })
export class NoticeService {
  private nextId = 1;
  private pageLoadedAt = 0;
  readonly notices = signal<Notice[]>([]);

  constructor() {
    inject(Router).events.subscribe((event) => {
      if (event instanceof NavigationStart) {
        const seenBefore = this.pageLoadedAt;
        this.notices.update((list) => list.filter((n) => n.raisedAt >= seenBefore));
      } else if (event instanceof NavigationEnd) {
        this.pageLoadedAt = performance.now();
      }
    });
  }

  show(text: string, kind: NoticeKind = 'success'): void {
    this.notices.update((list) => [
      ...list, { id: this.nextId++, kind, text, raisedAt: performance.now() },
    ]);
  }

  dismiss(id: number): void {
    this.notices.update((list) => list.filter((notice) => notice.id !== id));
  }
}
