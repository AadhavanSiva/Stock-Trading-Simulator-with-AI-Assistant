import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  Account, AccountDeleted, ActionReport, AssistantAnswer, AssistantStatus, AuthConfig,
  BuyPreview, ChartFetch, Leaderboard,
  LeaderboardSettingResult, Portfolio, PriceHistory, Purchase, Quote, Sale, SearchResult,
  SellPreview, SellQuote, StockDetail, TokenResponse, TradePage, User, WatchChange,
  Watchlist, WatchlistRefresh,
} from './models';

/** Typed calls to the FastAPI backend. Every path is relative: /api is
 *  proxied to the API by the dev server, and served beside it in production. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  authConfig(): Observable<AuthConfig> {
    return this.http.get<AuthConfig>('/api/auth/config');
  }

  signInWithGoogle(credential: string): Observable<TokenResponse> {
    return this.http.post<TokenResponse>('/api/auth/google', { credential });
  }

  signInLocally(email: string): Observable<TokenResponse> {
    return this.http.post<TokenResponse>('/api/auth/dev', { email });
  }

  me(): Observable<User> {
    return this.http.get<User>('/api/auth/me');
  }

  portfolio(): Observable<Portfolio> {
    return this.http.get<Portfolio>('/api/portfolio');
  }

  trades(page = 1): Observable<TradePage> {
    return this.http.get<TradePage>('/api/trades', { params: { page } });
  }

  history(): Observable<PriceHistory> {
    return this.http.get<PriceHistory>('/api/history');
  }

  search(q: string, limit = 8): Observable<SearchResult[]> {
    return this.http.get<SearchResult[]>('/api/search', { params: { q, limit } });
  }

  quote(symbol: string): Observable<Quote> {
    return this.http.get<Quote>('/api/quote', { params: { symbol } });
  }

  previewBuy(symbol: string, shares: string): Observable<BuyPreview> {
    return this.http.post<BuyPreview>('/api/buy/preview', { symbol, shares });
  }

  buy(symbol: string, shares: string): Observable<Purchase> {
    return this.http.post<Purchase>('/api/buy', { symbol, shares });
  }

  sellQuote(symbol: string): Observable<SellQuote> {
    return this.http.get<SellQuote>(`/api/sell/${encodeURIComponent(symbol)}`);
  }

  previewSell(symbol: string, shares: string): Observable<SellPreview> {
    return this.http.post<SellPreview>('/api/sell/preview', { symbol, shares });
  }

  sell(symbol: string, shares: string): Observable<Sale> {
    return this.http.post<Sale>('/api/sell', { symbol, shares });
  }

  stock(symbol: string, range?: string | null): Observable<StockDetail> {
    let params = new HttpParams();
    if (range) {
      params = params.set('range', range);
    }
    return this.http.get<StockDetail>(`/api/stocks/${encodeURIComponent(symbol)}`, { params });
  }

  fetchChart(symbol: string, range: string): Observable<ChartFetch> {
    return this.http.post<ChartFetch>(`/api/stocks/${encodeURIComponent(symbol)}/fetch`, { range });
  }

  watchlist(): Observable<Watchlist> {
    return this.http.get<Watchlist>('/api/watchlist');
  }

  follow(symbol: string): Observable<WatchChange> {
    return this.http.post<WatchChange>('/api/watchlist', { symbol });
  }

  unfollow(symbol: string): Observable<WatchChange> {
    return this.http.delete<WatchChange>(`/api/watchlist/${encodeURIComponent(symbol)}`);
  }

  refreshWatchlist(): Observable<WatchlistRefresh> {
    return this.http.post<WatchlistRefresh>('/api/watchlist/refresh', null);
  }

  leaderboard(): Observable<Leaderboard> {
    return this.http.get<Leaderboard>('/api/leaderboard');
  }

  account(): Observable<Account> {
    return this.http.get<Account>('/api/account');
  }

  setLeaderboard(optIn: boolean, name: string): Observable<LeaderboardSettingResult> {
    return this.http.post<LeaderboardSettingResult>(
      '/api/account/leaderboard', { opt_in: optIn, name });
  }

  /** The export as a file. Fetched rather than linked to, because a plain
   *  link cannot carry the access token. */
  exportAccount(): Observable<Blob> {
    return this.http.get('/api/account/export', { responseType: 'blob' });
  }

  deleteAccount(confirmation: string): Observable<AccountDeleted> {
    return this.http.post<AccountDeleted>('/api/account/delete', { confirmation });
  }

  refreshPrices(): Observable<ActionReport> {
    return this.http.post<ActionReport>('/api/actions/refresh-prices', null);
  }

  loadHistory(): Observable<ActionReport> {
    return this.http.post<ActionReport>('/api/actions/load-history', null);
  }

  assistantStatus(): Observable<AssistantStatus> {
    return this.http.get<AssistantStatus>('/api/assistant/status');
  }

  /** Only the words of earlier turns go back; sources and suggestion
   *  HTML stay in the browser. */
  ask(question: string, symbol: string,
      earlier: { question: string; answer: string }[]): Observable<AssistantAnswer> {
    return this.http.post<AssistantAnswer>('/api/assistant', { question, symbol, earlier });
  }
}

/** The message to show for a failed call. The API always answers
 *  {"detail": "..."} written for a person; anything else is a network
 *  failure or a proxy that could not reach the API. */
export function errorMessage(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    const detail = error.error?.detail;
    if (typeof detail === 'string' && detail) {
      return detail;
    }
    if (error.status === 0) {
      return 'Could not reach the server. Check that the API is running and try again.';
    }
  }
  return 'Something went wrong. Try again in a minute.';
}
