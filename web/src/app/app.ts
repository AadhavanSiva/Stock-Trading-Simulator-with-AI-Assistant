import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService } from './core/auth.service';
import { NoticeService } from './core/notice.service';
import { AskPanel } from './shared/ask-panel';
import { MoneyPipe } from './shared/format';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, MoneyPipe, AskPanel],
  templateUrl: './app.html',
})
export class App {
  protected readonly auth = inject(AuthService);
  protected readonly notices = inject(NoticeService);
}
