import os
import secrets

from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "practice")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_PORT = os.getenv("DB_PORT", "5432")

# Flask session signing key. Set FLASK_SECRET_KEY in .env to keep flash
# messages working across restarts; otherwise a fresh random key is
# generated per process. Never hardcode a real one.
SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

# --- Google sign-in -------------------------------------------------------
# Create these at console.cloud.google.com: APIs & Services > Credentials >
# OAuth client ID > Web application, with the redirect URI
#   http://127.0.0.1:5000/auth/callback
# Then put the two values in .env. The app tells you this on /login if they
# are missing, rather than failing with a stack trace.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

def google_configured():
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

# Opt-in local sign-in, for trying the app before wiring up Google. It is
# OFF unless you set ALLOW_DEV_LOGIN=1, and the UI says plainly when it is on.
ALLOW_DEV_LOGIN = os.getenv("ALLOW_DEV_LOGIN", "").strip() in ("1", "true", "yes")

# Which account the terminal interface acts as. The CLI cannot run a browser
# redirect, so it reads the account from here instead.
PORTFOLIO_USER = os.getenv("PORTFOLIO_USER")

# Cash every new account starts with, in dollars.
STARTING_CASH = os.getenv("STARTING_CASH", "50000")

# --- AI assistant -----------------------------------------------------------
# Runs on Google's Gemini API. The SDK reads the key from GEMINI_API_KEY (or
# GOOGLE_API_KEY) itself; nothing here reads or stores it. GEMINI_API_KEY is
# the one to use, so it is not confused with the GOOGLE_CLIENT_* sign-in
# settings above. Without a key the assistant explains how to add one.
ASSISTANT_ENABLED = os.getenv("ASSISTANT_ENABLED", "1").strip().lower() not in ("0", "false", "no")
ASSISTANT_MODEL = os.getenv("ASSISTANT_MODEL", "gemini-3.8-flash")
# How much the model reasons before answering: low, medium or high.
ASSISTANT_THINKING = os.getenv("ASSISTANT_THINKING", "medium").strip().lower()
# Let the assistant search Google for news, earnings and background that the
# app does not store. On Gemini 3 models each search it runs is billed.
ASSISTANT_SEARCH = os.getenv("ASSISTANT_SEARCH", "1").strip().lower() not in ("0", "false", "no")
