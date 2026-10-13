import { Routes } from '@angular/router';

import { authGuard, signedOutGuard, signedOutMatch } from './core/auth.guard';

export const routes: Routes = [
  {
    path: 'login',
    canActivate: [signedOutGuard],
    title: 'Log in · Portfolio Tracker',
    loadComponent: () => import('./pages/login/login').then((m) => m.Login),
  },
  {
    // The front door: signed-in readers fall through to the portfolio.
    path: '',
    pathMatch: 'full',
    canMatch: [signedOutMatch],
    title: 'Portfolio Tracker · Learn how investing works',
    loadComponent: () => import('./pages/landing/landing').then((m) => m.Landing),
  },
  {
    // The same Google flow as /login, framed for someone arriving new.
    path: 'signup',
    canActivate: [signedOutGuard],
    title: 'Open an account · Portfolio Tracker',
    data: { mode: 'signup' },
    loadComponent: () => import('./pages/login/login').then((m) => m.Login),
  },
  // Readable signed out: someone deciding whether to sign up needs to be
  // able to read these first.
  {
    path: 'terms',
    title: 'Terms of Service · Portfolio Tracker',
    loadComponent: () => import('./pages/legal/terms').then((m) => m.Terms),
  },
  {
    path: 'privacy',
    title: 'Privacy Policy · Portfolio Tracker',
    loadComponent: () => import('./pages/legal/privacy').then((m) => m.Privacy),
  },
  {
    path: 'disclaimer',
    title: 'Disclaimer · Portfolio Tracker',
    loadComponent: () => import('./pages/legal/disclaimer').then((m) => m.Disclaimer),
  },
  {
    // Signed out by the time it is shown, so it sits outside the guard.
    path: 'account/deleted',
    title: 'Account deleted · Portfolio Tracker',
    loadComponent: () => import('./pages/account/account-deleted').then((m) => m.AccountDeleted),
  },
  {
    path: '',
    canActivate: [authGuard],
    children: [
      {
        path: '',
        pathMatch: 'full',
        title: 'Portfolio · Portfolio Tracker',
        loadComponent: () => import('./pages/portfolio/portfolio').then((m) => m.Portfolio),
      },
      {
        path: 'balance',
        title: 'Balance · Portfolio Tracker',
        loadComponent: () => import('./pages/balance/balance').then((m) => m.Balance),
      },
      {
        path: 'trades',
        title: 'Activity · Portfolio Tracker',
        loadComponent: () => import('./pages/trades/trades').then((m) => m.Trades),
      },
      {
        path: 'history',
        title: 'Price history · Portfolio Tracker',
        loadComponent: () => import('./pages/history/history').then((m) => m.History),
      },
      {
        path: 'watchlist',
        title: 'Watchlist · Portfolio Tracker',
        loadComponent: () => import('./pages/watchlist/watchlist').then((m) => m.Watchlist),
      },
      {
        path: 'leaderboard',
        title: 'Leaderboard · Portfolio Tracker',
        loadComponent: () => import('./pages/leaderboard/leaderboard').then((m) => m.Leaderboard),
      },
      {
        path: 'account',
        title: 'Account · Portfolio Tracker',
        loadComponent: () => import('./pages/account/account').then((m) => m.Account),
      },
      {
        path: 'actions',
        title: 'Update data · Portfolio Tracker',
        loadComponent: () => import('./pages/actions/actions').then((m) => m.Actions),
      },
      {
        path: 'buy',
        title: 'Buy · Portfolio Tracker',
        loadComponent: () => import('./pages/buy/buy').then((m) => m.Buy),
      },
      {
        path: 'sell',
        title: 'Sell · Portfolio Tracker',
        loadComponent: () => import('./pages/sell/sell-list').then((m) => m.SellList),
      },
      {
        path: 'sell/:symbol',
        title: 'Sell · Portfolio Tracker',
        loadComponent: () => import('./pages/sell/sell').then((m) => m.Sell),
      },
      {
        path: 'stock/:symbol',
        title: 'Stock · Portfolio Tracker',
        loadComponent: () => import('./pages/stock/stock').then((m) => m.Stock),
      },
      {
        path: 'search',
        title: 'Search · Portfolio Tracker',
        loadComponent: () => import('./pages/search/search').then((m) => m.Search),
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
