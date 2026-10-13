import { Component, computed, input, signal } from '@angular/core';

import { Chart, Readout } from '../core/models';

/** A price line drawn from a chart the API has already projected.
 *
 *  The server does the projection, thinning and axis ticks
 *  (portfolio_tracker/charts.py), so this only draws paths and labels and
 *  finds the point under the pointer. The plot stretches to its container,
 *  which is why readouts and ticks are positioned in percentages. */
@Component({
  selector: 'app-price-chart',
  template: `
    @let c = chart();
    <figure class="chart" [class.falling]="falling()">
      <div class="chart-plot"
           (pointermove)="track($event)" (pointerleave)="active.set(null)">
        <svg [attr.viewBox]="'0 0 ' + c.width + ' ' + c.height" preserveAspectRatio="none"
             role="img" [attr.aria-label]="label()">
          @for (tick of c.y_ticks; track tick.label) {
            <line class="chart-grid" x1="0" [attr.x2]="c.width" [attr.y1]="tick.y" [attr.y2]="tick.y" />
          }
          <line class="chart-baseline" x1="0" [attr.x2]="c.width"
                [attr.y1]="c.baseline_y" [attr.y2]="c.baseline_y" />
          <path class="chart-area" [attr.d]="c.area_path" />
          <path class="chart-line" [attr.d]="c.path" vector-effect="non-scaling-stroke" />
        </svg>
        @for (tick of c.y_ticks; track tick.label) {
          <span class="chart-y" [style.top.%]="tick.pct">{{ tick.label }}</span>
        }
        @if (active(); as point) {
          <span class="chart-cursor" [style.left.%]="point.x"></span>
          <span class="chart-dot" [style.left.%]="point.x" [style.top.%]="point.y"></span>
          <span class="chart-tip" [style.left.%]="point.x"
                [class.flip]="point.x > 70">
            <strong>{{ point.price }}</strong> {{ point.when }}
          </span>
        }
      </div>
      <div class="chart-x" aria-hidden="true">
        @for (tick of c.x_ticks; track tick.pct) {
          <span [style.left.%]="tick.pct" [class]="tick.edge">{{ tick.label }}</span>
        }
      </div>
    </figure>
  `,
})
export class PriceChart {
  readonly chart = input.required<Chart>();

  protected readonly active = signal<Readout | null>(null);
  protected readonly falling = computed(() => Number(this.chart().change) < 0);
  protected readonly label = computed(() => {
    const c = this.chart();
    const first = c.readouts[0];
    const last = c.readouts[c.readouts.length - 1];
    return first && last
      ? `Price chart from ${first.price} on ${first.when} to ${last.price} on ${last.when}.`
      : 'Price chart';
  });

  protected track(event: PointerEvent): void {
    const readouts = this.chart().readouts;
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    if (!readouts.length || box.width === 0) {
      return;
    }
    const pct = ((event.clientX - box.left) / box.width) * 100;
    // Readouts are in x order, so a binary search finds the nearest.
    let lo = 0;
    let hi = readouts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (readouts[mid].x < pct) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    const before = readouts[Math.max(0, lo - 1)];
    const nearest = Math.abs(before.x - pct) < Math.abs(readouts[lo].x - pct) ? before : readouts[lo];
    this.active.set(nearest);
  }
}
