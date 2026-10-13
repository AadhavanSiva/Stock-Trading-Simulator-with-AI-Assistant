import { Component, input } from '@angular/core';

/** The banner shared by all three policy pages.
 *
 *  These pages describe what the application actually does, which is the
 *  part a developer can supply. They have not been written or reviewed by
 *  a lawyer, and the banner says so plainly. It should be removed only
 *  once the documents behind it have actually been reviewed. */
@Component({
  selector: 'app-draft-notice',
  template: `
    <div class="draft-notice" role="note">
      <p>
        <strong>Draft — not a finished legal document.</strong>
        This {{ what() }} is a template. It describes how this application
        behaves, but it has not been written or reviewed by a lawyer and is
        not tailored to any jurisdiction.
      </p>
      <p>
        Portfolio Tracker is a personal, educational project. It uses simulated
        money only: no real money is deposited, traded or paid out. This
        {{ what() }} is here so the project has something accurate to point at.
      </p>
    </div>
  `,
})
export class DraftNotice {
  readonly what = input.required<string>();
}
