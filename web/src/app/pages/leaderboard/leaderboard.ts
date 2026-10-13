import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { ApiService, errorMessage } from '../../core/api.service';
import { Leaderboard as LeaderboardData } from '../../core/models';
import { FORMAT_PIPES } from '../../shared/format';

/** Ranked by account value, among those who asked to be listed. */
@Component({
  selector: 'app-leaderboard',
  imports: [RouterLink, ...FORMAT_PIPES],
  templateUrl: './leaderboard.html',
})
export class Leaderboard implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly data = signal<LeaderboardData | null>(null);
  protected readonly error = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  protected load(): void {
    this.error.set(null);
    this.api.leaderboard().subscribe({
      next: (data) => this.data.set(data),
      error: (err) => this.error.set(errorMessage(err)),
    });
  }
}
