import { ComponentFixture, TestBed } from '@angular/core/testing';

import { Delta } from './delta';

describe('Delta', () => {
  let fixture: ComponentFixture<Delta>;

  function render(amount: string | null, percent?: string | null): HTMLElement {
    fixture = TestBed.createComponent(Delta);
    fixture.componentRef.setInput('amount', amount);
    if (percent !== undefined) {
      fixture.componentRef.setInput('percent', percent);
    }
    fixture.detectChanges();
    return fixture.nativeElement.querySelector('.delta');
  }

  it('shows a loss with arrow, sign, word and colour class', () => {
    const el = render('-2.62', '-0.77');
    expect(el.classList).toContain('loss');
    expect(el.textContent).toContain('▼');
    expect(el.textContent).toContain('down');
    expect(el.textContent).toContain('-2.62');
    expect(el.textContent).toContain('(-0.8%)');
  });

  it('shows a dash when there is nothing to compare', () => {
    const el = render(null);
    expect(el.classList).toContain('unknown');
    expect(el.textContent).toContain('—');
  });
});
