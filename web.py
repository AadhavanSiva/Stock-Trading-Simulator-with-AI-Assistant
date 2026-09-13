"""Flask front end for the portfolio tracker.

This is a view layer and nothing more. Every database query lives in
portfolio_tracker/models/, every market-data call lives in
portfolio_tracker/services/market_data.py, and the logic that sits above
them lives in portfolio_tracker/operations.py. Routes here parse a form,
call one of those, and render the result — the same way cli.py does.

Run it with:  python -m flask --app web run
"""
from decimal import Decimal
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import (
    Flask, abort, flash, g, redirect, render_template, request, session, url_for,
)

from portfolio_tracker import config, operations
from portfolio_tracker.errors import InsufficientFunds, ValidationError
from portfolio_tracker.models import history, portfolio, users

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY

oauth = OAuth(app)
if config.google_configured():
    oauth.register(
        name="google",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        server_metadata_url=config.GOOGLE_DISCOVERY_URL,
        client_kwargs={"scope": "openid email profile"},
    )


# ------------------------------------------------------------------- auth

def login_required(view):
    """Gate a page behind a signed-in account.

    Also loads the account onto `g` so the view and its template never
    have to re-fetch it. A session pointing at a deleted account is
    cleared rather than 500ing.
    """
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if user_id is None:
            return redirect(url_for("login", next=request.path))

        account = users.get_by_id(user_id)
        if account is None:
            session.clear()
            flash("That account no longer exists. Please sign in again.", "notice")
            return redirect(url_for("login"))

        g.user = account
        g.user_id = account[0]
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_user():
    """Make the signed-in account available to every template."""
    account = getattr(g, "user", None)
    if account is None:
        return {"current_user": None}
    _, _, email, display_name, cash = account
    return {"current_user": {
        "email": email,
        "display_name": display_name or email,
        "cash": cash,
    }}


def safe_next(target):
    """Return `target` only if it is a path on this site.

    Sending the browser to an arbitrary `next` value is an open redirect —
    "https://evil.example/x" or the protocol-relative "//evil.example/x"
    would both leave the site while looking like an internal link. Only a
    single-slash relative path is allowed through.
    """
    if not target:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    if "\\" in target:
        return None
    return target


def finish_login(user_id, message):
    """Complete a sign-in: set the session and return where they were headed."""
    session["user_id"] = user_id
    destination = safe_next(session.pop("next", None)) or url_for("index")
    flash(message, "success")
    return redirect(destination)


@app.route("/login")
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))

    # Remembered in the session because the Google round trip does not
    # carry our own query parameters back.
    destination = safe_next(request.args.get("next"))
    if destination:
        session["next"] = destination

    return render_template(
        "login.html",
        google_ready=config.google_configured(),
        dev_login=config.ALLOW_DEV_LOGIN,
        redirect_uri=url_for("auth_callback", _external=True),
        starting_cash=users.starting_cash(),
    )


@app.route("/login/google")
def login_google():
    if not config.google_configured():
        flash("Google sign-in is not configured yet.", "error")
        return redirect(url_for("login"))
    return oauth.google.authorize_redirect(url_for("auth_callback", _external=True))


@app.route("/auth/callback")
def auth_callback():
    if not config.google_configured():
        return redirect(url_for("login"))

    try:
        token = oauth.google.authorize_access_token()
    except Exception as exc:
        flash(f"Google sign-in failed: {exc}", "error")
        return redirect(url_for("login"))

    claims = token.get("userinfo") or {}
    # `sub` is Google's stable account id. Email can change; sub cannot.
    if not claims.get("sub"):
        flash("Google did not return an account id. Sign-in cancelled.", "error")
        return redirect(url_for("login"))

    account = users.upsert_from_google(
        claims["sub"], claims.get("email", ""), claims.get("name")
    )
    return finish_login(
        account[0], f"Signed in as {claims.get('email', 'your account')}."
    )


@app.route("/login/dev", methods=["POST"])
def login_dev():
    """Local-only sign-in for trying the app before Google is configured.

    Disabled unless ALLOW_DEV_LOGIN is set, and never a substitute for real
    auth — it exists so the app is usable on a laptop without a Cloud
    Console project.
    """
    if not config.ALLOW_DEV_LOGIN:
        abort(404)

    email = (request.form.get("email") or "").strip().lower()
    if not email:
        flash("Enter an email address to sign in with.", "error")
        return redirect(url_for("login"))

    account = users.upsert_from_google(f"dev:{email}", email, email.split("@")[0])
    return finish_login(account[0], f"Signed in locally as {email}.")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been signed out.", "notice")
    return redirect(url_for("login"))


# ---------------------------------------------------------------- filters
# Presentation only. Anything that decides a number belongs in operations.

@app.template_filter("money")
def money(value):
    """1234.5 -> $1,234.50"""
    if value is None:
        return "—"
    return f"${Decimal(value):,.2f}"


@app.template_filter("signed_money")
def signed_money(value):
    """A gain or loss always carries its sign, never just a colour."""
    if value is None:
        return "—"
    return f"{Decimal(value):+,.2f}"


@app.template_filter("shares")
def shares(value):
    if value is None:
        return "—"
    return operations.format_shares(value)


@app.template_filter("percent")
def percent(value):
    if value is None:
        return "—"
    value = Decimal(value)
    # A tiny cost basis can produce a four-digit percentage.
    digits = 0 if abs(value) >= 1000 else 1
    return f"{value:+.{digits}f}%"


def movement(amount):
    """Label a change so it reads without relying on colour.

    Returns (direction, arrow, word) for templates to render alongside
    the number. Colour is applied on top of this, never instead of it.
    """
    if amount is None:
        return ("unknown", "", "not priced yet")
    amount = Decimal(amount)
    if amount > 0:
        return ("gain", "▲", "up")
    if amount < 0:
        return ("loss", "▼", "down")
    return ("flat", "–", "no change")


app.jinja_env.globals["movement"] = movement


# -------------------------------------------------------------- portfolio

@app.route("/")
@login_required
def index():
    return render_template("portfolio.html", summary=operations.account_summary(g.user_id))


@app.route("/balance")
@login_required
def balance():
    return render_template("balance.html", summary=operations.account_summary(g.user_id))


# -------------------------------------------------------------------- buy

@app.route("/buy")
@login_required
def buy_start():
    return render_template("buy.html", symbol=request.args.get("symbol", ""))


@app.route("/buy/lookup", methods=["POST"])
@login_required
def buy_lookup():
    """Step 1: price the ticker. Nothing is written yet."""
    symbol = request.form.get("symbol", "")
    try:
        quote = operations.look_up(g.user_id, symbol)
    except ValidationError as exc:
        return render_template("buy.html", symbol=symbol, error=str(exc)), 400

    return render_template("buy_quantity.html", quote=quote)


@app.route("/buy/review", methods=["POST"])
@login_required
def buy_review():
    """Step 2: show what the purchase will cost, before anything happens."""
    symbol = request.form.get("symbol", "")
    raw_shares = request.form.get("shares", "")

    try:
        quote = operations.look_up(g.user_id, symbol)
    except ValidationError as exc:
        return render_template("buy.html", symbol=symbol, error=str(exc)), 400

    try:
        # Server-side validation. The browser's number input is a
        # convenience; this is the check that counts.
        shares = operations.parse_quantity(raw_shares)
    except ValidationError as exc:
        return render_template(
            "buy_quantity.html", quote=quote, error=str(exc), shares=raw_shares
        ), 400

    cost = operations.to_money(shares * quote.price)
    if cost > quote.cash:
        return render_template(
            "buy_quantity.html", quote=quote, shares=raw_shares,
            error=str(InsufficientFunds(cost, quote.cash)),
        ), 400

    return render_template(
        "buy_confirm.html", quote=quote, shares=shares, cost=cost,
        cash_after=quote.cash - cost,
    )


@app.route("/buy/confirm", methods=["POST"])
@login_required
def buy_confirm():
    """Step 3: the only step that writes."""
    symbol = request.form.get("symbol", "")
    raw_shares = request.form.get("shares", "")

    try:
        purchase = operations.buy(g.user_id, symbol, raw_shares)
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("buy_start", symbol=symbol))

    flash(
        f"Bought {operations.format_shares(purchase.shares)} "
        f"{'share' if purchase.shares == 1 else 'shares'} of {purchase.symbol} "
        f"at ${purchase.price:,.2f} each. Cash left: ${purchase.cash:,.2f}.",
        "success",
    )
    return redirect(url_for("index"))


# ------------------------------------------------------------------- sell

@app.route("/sell")
@login_required
def sell_start():
    return render_template("sell.html", summary=operations.account_summary(g.user_id))


@app.route("/sell/<symbol>")
@login_required
def sell_quantity(symbol):
    symbol = symbol.upper()
    held = portfolio.get_holding(g.user_id, symbol)
    if held is None:
        flash(f"You do not own any {symbol}.", "error")
        return redirect(url_for("sell_start"))

    owned, average_cost = held
    try:
        quote = operations.look_up(g.user_id, symbol)
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("sell_start"))

    return render_template(
        "sell_quantity.html",
        symbol=symbol,
        company_name=quote.company_name,
        owned=owned,
        # Truncated, never rounded: a rounded-up maximum would be
        # rejected as an oversell the moment it was submitted back.
        maximum=operations.sellable_maximum(owned),
        average_cost=average_cost,
        price=quote.price,
    )


@app.route("/sell/<symbol>/review", methods=["POST"])
@login_required
def sell_review(symbol):
    symbol = symbol.upper()
    held = portfolio.get_holding(g.user_id, symbol)
    if held is None:
        flash(f"You do not own any {symbol}.", "error")
        return redirect(url_for("sell_start"))

    owned, average_cost = held
    raw_shares = request.form.get("shares", "")

    try:
        quote = operations.look_up(g.user_id, symbol)
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("sell_start"))

    try:
        shares = operations.parse_quantity(raw_shares, maximum=owned)
    except ValidationError as exc:
        return render_template(
            "sell_quantity.html",
            symbol=symbol,
            company_name=quote.company_name,
            owned=owned,
            maximum=operations.sellable_maximum(owned),
            average_cost=average_cost,
            price=quote.price,
            error=str(exc),
            shares=raw_shares,
        ), 400

    proceeds = operations.to_money(shares * quote.price)
    return render_template(
        "sell_confirm.html",
        symbol=symbol,
        company_name=quote.company_name,
        shares=shares,
        price=quote.price,
        proceeds=proceeds,
        gain=operations.to_money((quote.price - average_cost) * shares),
        closes=shares == owned,
        cash_after=quote.cash + proceeds,
    )


@app.route("/sell/<symbol>/confirm", methods=["POST"])
@login_required
def sell_confirm(symbol):
    symbol = symbol.upper()
    raw_shares = request.form.get("shares", "")

    try:
        sale = operations.sell(g.user_id, symbol, raw_shares)
    except ValidationError as exc:
        flash(str(exc), "error")
        return redirect(url_for("sell_start"))

    message = (
        f"Sold {operations.format_shares(sale.shares)} "
        f"{'share' if sale.shares == 1 else 'shares'} of {sale.symbol} "
        f"for ${sale.proceeds:,.2f}."
    )
    message += (
        f" Your {sale.symbol} position is now closed." if sale.closed
        else f" You still own {operations.format_shares(sale.remaining)} shares."
    )
    message += f" Cash available: ${sale.cash:,.2f}."
    flash(message, "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- history

@app.route("/history")
@login_required
def history_view():
    averages = {symbol: avg for symbol, avg in history.get_recent_averages(30)}
    highs_lows = {symbol: (high, low) for symbol, high, low in history.get_high_low()}

    rows = []
    for symbol, name, held, paid, current, gain in portfolio.get_holdings_with_details(g.user_id):
        high, low = highs_lows.get(symbol, (None, None))
        rows.append({
            "symbol": symbol,
            "company_name": name or symbol,
            "average": averages.get(symbol),
            "high": high,
            "low": low,
            "current_price": current,
        })

    return render_template("history.html", rows=rows, days=30)


# ---------------------------------------------------------------- actions

@app.route("/actions")
@login_required
def actions():
    return render_template(
        "actions.html", holdings=portfolio.get_portfolio_symbols(g.user_id)
    )


@app.route("/actions/refresh-prices", methods=["POST"])
@login_required
def refresh_prices():
    report = operations.refresh_prices(g.user_id)
    if not report.total:
        flash("You have no holdings yet, so there were no prices to refresh.", "notice")
        return redirect(url_for("actions"))

    flash(
        f"Refreshed {report.updated} of {report.total} "
        f"{'price' if report.total == 1 else 'prices'}."
        + (f" {report.failed} could not be updated." if report.failed else ""),
        "error" if report.failed and not report.updated else "success",
    )
    return render_template(
        "actions.html",
        holdings=portfolio.get_portfolio_symbols(g.user_id),
        report=report,
        report_title="Price refresh",
    )


@app.route("/actions/load-history", methods=["POST"])
@login_required
def load_history():
    report = operations.load_all_history(g.user_id)
    if not report.total:
        flash("You have no holdings yet, so there was no history to load.", "notice")
        return redirect(url_for("actions"))

    flash(
        f"Checked {report.total} {'holding' if report.total == 1 else 'holdings'} "
        f"and stored {report.new_rows} new "
        f"{'day' if report.new_rows == 1 else 'days'} of prices.",
        "success",
    )
    return render_template(
        "actions.html",
        holdings=portfolio.get_portfolio_symbols(g.user_id),
        report=report,
        report_title="Historical data load",
    )


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html",
                           code=404,
                           message="That page does not exist."), 404


if __name__ == "__main__":
    app.run(debug=True)
