import os

database_url = os.environ["DATABASE_URL"]
api_key = os.environ.get("API_KEY")
port = os.getenv("PORT")

debug = os.getenv("DEBUG")
payment_secret = os.environ["PAYMENTS_WEBHOOK_SECRET"]

admin_key = os.getenv("ADMIN_SECRET")
