// Shapes returned by the FastAPI backend (api/schemas.py).
//
// Money, prices and share counts arrive as strings ("1234.50"), never
// numbers: JSON numbers are floats, and money must not pass through one.
// The UI only formats these for display; it never does arithmetic on them.
export type Decimal = string;

export interface AuthConfig {
  google_client_id: string | null;
  dev_login: boolean;
  starting_cash: Decimal;
}

export interface User {
  id: number;
  email: string;
  display_name: string;
  cash: Decimal;
  starting_cash: Decimal;
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  new_account: boolean;
  user: User;
}

export interface YTick { value: Decimal; label: string; y: number; pct: number; }
export interface XTick { label: string; pct: number; edge: 'start' | 'middle' | 'end'; }
export interface Readout { x: number; y: number; when: string; price: string; }

export interface Chart {
  width: number;
  height: number;
  path: string;
  area_path: string;
  baseline_y: number;
  low: Decimal;
  high: Decimal;
  average: Decimal;
  first: Decimal;
  last: Decimal;
  change: Decimal;
  change_pct: Decimal;
  count: number;
  plotted: number;
  y_ticks: YTick[];
  x_ticks: XTick[];
  readouts: Readout[];
}

export interface Holding {
  symbol: string;
  company_name: string;
  shares: Decimal;
  purchase_price: Decimal;
  current_price: Decimal | null;
  cost: Decimal;
  value: Decimal | null;
  gain_loss: Decimal | null;
  percent: Decimal | null;
  previous_close: Decimal | null;
  todays_change: Decimal | null;
  todays_percent: Decimal | null;
  realized: Decimal | null;
}

export interface Trade {
  id: number;
  symbol: string;
  company_name: string;
  side: 'buy' | 'sell';
  shares: Decimal;
  price: Decimal;
  total_value: Decimal;
  cost_basis: Decimal | null;
  gain: Decimal | null;
  opening: boolean;
  traded_at: string;
}

export interface ClosedPosition {
  symbol: string;
  realized: Decimal;
  proceeds: Decimal;
  shares_sold: Decimal;
}

export interface Performance {
  chart: Chart;
  start_value: Decimal;
  end_value: Decimal;
  change: Decimal;
  percent: Decimal | null;
  samples: number;
  first_at: string;
  last_at: string;
}

export interface Portfolio {
  holdings: Holding[];
  unpriced: string[];
  cash: Decimal;
  holdings_value: Decimal;
  total_cost: Decimal;
  total_gain: Decimal;
  total_percent: Decimal;
  net_worth: Decimal;
  starting_cash: Decimal;
  total_return: Decimal | null;
  todays_change: Decimal | null;
  todays_percent: Decimal | null;
  realized_total: Decimal;
  closed_positions: ClosedPosition[];
  activity: Trade[];
  performance: Performance | null;
  prices_as_of: string | null;
}

export interface TradePage {
  trades: Trade[];
  page: number;
  pages: number;
  total: number;
  page_size: number;
  realized_total: Decimal;
  closed_positions: ClosedPosition[];
}

export interface PriceRange {
  symbol: string;
  company_name: string;
  current_price: Decimal | null;
  average: Decimal | null;
  high: Decimal | null;
  low: Decimal | null;
}

export interface PriceHistory { days: number; rows: PriceRange[]; }

export interface SearchResult { symbol: string; name: string; }

export interface Quote {
  symbol: string;
  company_name: string;
  price: Decimal;
  previous_close: Decimal | null;
  owned: Decimal | null;
  average_cost: Decimal | null;
  cash: Decimal;
  affordable: Decimal;
}

export interface BuyPreview { quote: Quote; shares: Decimal; cost: Decimal; cash_after: Decimal; }

export interface Purchase {
  symbol: string;
  company_name: string;
  price: Decimal;
  shares: Decimal;
  cost: Decimal;
  total_shares: Decimal;
  average_cost: Decimal;
  cash: Decimal;
  message: string;
}

export interface SellQuote {
  symbol: string;
  company_name: string;
  owned: Decimal;
  maximum: Decimal;
  average_cost: Decimal;
  price: Decimal;
  cash: Decimal;
}

export interface SellPreview {
  symbol: string;
  company_name: string;
  shares: Decimal;
  price: Decimal;
  proceeds: Decimal;
  gain: Decimal;
  closes: boolean;
  cash_after: Decimal;
}

export interface Sale {
  symbol: string;
  shares: Decimal;
  price: Decimal;
  proceeds: Decimal;
  gain: Decimal;
  remaining: Decimal;
  closed: boolean;
  cash: Decimal;
  message: string;
}

export interface RangeOption { key: string; label: string; }

export interface StockDetail {
  symbol: string;
  quote: Quote;
  watching: boolean;
  prices_as_of: string | null;
  earliest_available: string;
  range: string;
  range_label: string;
  ranges: RangeOption[];
  intraday: boolean;
  chart: Chart | null;
  needs_fetch: boolean;
  partial: boolean;
  stored_since: string | null;
  stats: { low: Decimal | null; high: Decimal | null; average: Decimal | null };
}

export interface ChartFetch { symbol: string; range: string; added: number; message: string; }

export interface WatchRow {
  symbol: string;
  company_name: string;
  price: Decimal | null;
  previous_close: Decimal | null;
  todays_change: Decimal | null;
  todays_percent: Decimal | null;
  owned: boolean;
  added_at: string;
}

export interface Watchlist { rows: WatchRow[]; prices_as_of: string | null; }
export interface WatchChange { symbol: string; changed: boolean; message: string; }
export interface WatchlistRefresh { updated: number; failed: number; total: number; message: string; }

export interface LeaderboardRow {
  rank: number;
  name: string;
  total_value: Decimal;
  percent_return: Decimal | null;
  is_you: boolean;
}

export interface Standing {
  rank: number;
  total_value: Decimal;
  percent_return: Decimal | null;
  opted_in: boolean;
  participants: number;
  as_of: string;
}

export interface Leaderboard {
  rows: LeaderboardRow[];
  standing: Standing | null;
  starting_cash: Decimal;
}

export interface LeaderboardSettingResult {
  opt_in: boolean;
  name: string | null;
  standing: Standing | null;
  message: string;
}

export interface Account {
  email: string;
  display_name: string;
  cash: Decimal;
  starting_cash: Decimal;
  trade_count: number;
  delete_confirmation: string;
  leaderboard_opt_in: boolean;
  leaderboard_name: string | null;
  leaderboard_name_max: number;
  standing: Standing | null;
}

export interface AccountDeleted { email: string; }

export interface AssistantStatus {
  enabled: boolean;
  research: boolean;
  research_notice: string;
  max_question_chars: number;
}

export interface AssistantSource { title: string; uri: string; domain: string; }

export type AssistantKind =
  'ok' | 'invalid' | 'busy' | 'unavailable' | 'disabled' | 'refused' | 'failed' | 'network' | 'aborted';

export interface AssistantAnswer {
  ok: boolean;
  answer: string;
  kind: AssistantKind;
  message: string;
  sources: AssistantSource[];
  search_suggestions: string;
  notice: string;
  searches: string[];
  retry_after: number | null;
  research: boolean;
}

export interface SymbolOutcome { symbol: string; ok: boolean; message: string; }

export interface ActionReport {
  title: string;
  message: string;
  kind: 'success' | 'notice' | 'error';
  total: number;
  outcomes: SymbolOutcome[];
}
