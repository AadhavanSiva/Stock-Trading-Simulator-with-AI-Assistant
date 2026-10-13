import { Component, DestroyRef, inject, input, model, output, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Subject, catchError, debounceTime, distinctUntilChanged, of, switchMap } from 'rxjs';

import { ApiService } from '../core/api.service';
import { SearchResult } from '../core/models';

/** A ticker box that suggests companies as you type.
 *
 *  Typing a ticker and pressing Enter always works: suggestions are an
 *  aid for someone who knows the company name but not its symbol, never
 *  a gate. Arrow keys move through them; Enter or a click picks one. */
@Component({
  selector: 'app-symbol-search',
  imports: [FormsModule],
  template: `
    <div class="combo">
      <label [for]="inputId()">{{ label() }}</label>
      <input [id]="inputId()" name="symbol" type="text" autocomplete="off" spellcheck="false"
             role="combobox" aria-autocomplete="list" [attr.aria-expanded]="results().length > 0"
             [attr.aria-controls]="inputId() + '-list'"
             [attr.aria-activedescendant]="highlighted() >= 0 ? inputId() + '-opt-' + highlighted() : null"
             placeholder="Ticker or company, e.g. AAPL or Apple"
             [ngModel]="value()" (ngModelChange)="typed($event)"
             (keydown)="key($event)" (blur)="close()" />
      @if (results().length) {
        <ul class="combo-list" role="listbox" [id]="inputId() + '-list'">
          @for (r of results(); track r.symbol; let i = $index) {
            <li role="option" [id]="inputId() + '-opt-' + i" [attr.aria-selected]="i === highlighted()"
                [class.active]="i === highlighted()" (mousedown)="$event.preventDefault(); pick(r)">
              <strong>{{ r.symbol }}</strong> <span>{{ r.name }}</span>
            </li>
          }
        </ul>
      }
    </div>
  `,
})
export class SymbolSearch {
  private readonly api = inject(ApiService);

  readonly label = input('Stock');
  readonly inputId = input('symbol');
  readonly value = model('');
  /** A suggestion was chosen, or Enter was pressed on typed text. */
  readonly chosen = output<string>();

  protected readonly results = signal<SearchResult[]>([]);
  protected readonly highlighted = signal(-1);
  private readonly queries = new Subject<string>();

  constructor() {
    this.queries.pipe(
      debounceTime(200),
      distinctUntilChanged(),
      switchMap((q) => q.trim() ? this.api.search(q).pipe(catchError(() => of([]))) : of([])),
      takeUntilDestroyed(inject(DestroyRef)),
    ).subscribe((results) => {
      this.results.set(results);
      this.highlighted.set(-1);
    });
  }

  protected typed(text: string): void {
    this.value.set(text);
    this.queries.next(text);
  }

  protected key(event: KeyboardEvent): void {
    const count = this.results().length;
    if (event.key === 'ArrowDown' && count) {
      event.preventDefault();
      this.highlighted.update((i) => (i + 1) % count);
    } else if (event.key === 'ArrowUp' && count) {
      event.preventDefault();
      this.highlighted.update((i) => (i <= 0 ? count - 1 : i - 1));
    } else if (event.key === 'Escape') {
      this.close();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const pickedIndex = this.highlighted();
      if (pickedIndex >= 0) {
        this.pick(this.results()[pickedIndex]);
      } else if (this.value().trim()) {
        this.close();
        this.chosen.emit(this.value().trim());
      }
    }
  }

  protected pick(result: SearchResult): void {
    this.value.set(result.symbol);
    this.close();
    this.chosen.emit(result.symbol);
  }

  protected close(): void {
    this.results.set([]);
    this.highlighted.set(-1);
  }
}
