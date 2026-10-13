import { Component, computed, input } from '@angular/core';

import { PercentPipe, SignedMoneyPipe, movement } from './format';

/** A change in value: arrow, sign, amount and percent. Colour is added on
 *  top of these, never instead of them. */
@Component({
  selector: 'app-delta',
  imports: [SignedMoneyPipe, PercentPipe],
  // Kept on as few lines as possible: whitespace between these spans
  // would show up inside the parentheses the delta is often wrapped in.
  template: `<span class="delta" [class]="move().direction">@if (move().arrow) {<span class="arrow" aria-hidden="true">{{ move().arrow }}</span>}<span class="visually-hidden">{{ move().word }}</span>@if (amount() !== null) {{{ amount() | signedMoney }}} @else {—}@if (percent() !== null && percent() !== undefined) {<span class="delta-pct">({{ percent() | percent }})</span>}</span>`,
})
export class Delta {
  readonly amount = input<string | null>(null);
  readonly percent = input<string | null | undefined>(undefined);
  protected readonly move = computed(() => movement(this.amount()));
}
