"""
Beer Stripe - PC Receiver
Runs on your main computer. Listens for taps sent from the
Chromebook and stores them in its own SQLite database as a backup.
Also serves a small admin dashboard for viewing stock and restocking.
"""

from flask import Flask, request, jsonify, render_template
import sqlite3
import requests
import os
from datetime import datetime, timedelta

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "beers_backup.db")

# --- EDIT THESE for your setup ---
CHROMEBOOK_URL = "http://10.0.0.3:5000/api/restock"  # Chromebook's WireGuard IP
ADMIN_PASSWORD = "changeme"  # must match the value in beer-tracker/app.py
# -----------------------------------


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS beers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            received_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS restocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            received_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_stock_stats():
    """Computes stock stats from the backup data (mirrors the Chromebook's logic)."""
    conn = get_db()
    restock_row = conn.execute(
        "SELECT amount, timestamp FROM restocks ORDER BY id DESC LIMIT 1"
    ).fetchone()

    if restock_row is None:
        bought = 0
        restocked_at = "1970-01-01T00:00:00"
    else:
        bought = restock_row["amount"]
        restocked_at = restock_row["timestamp"]

    drunk_since_restock = conn.execute(
        "SELECT COUNT(*) as c FROM beers WHERE timestamp > ?", (restocked_at,)
    ).fetchone()["c"]

    last_24h_cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    pace_24h = conn.execute(
        "SELECT COUNT(*) as c FROM beers WHERE timestamp > ?", (last_24h_cutoff,)
    ).fetchone()["c"]

    conn.close()

    return {
        "bought": bought,
        "drunk": drunk_since_restock,
        "available": bought - drunk_since_restock,
        "pace_24h": pace_24h,
        "restocked_at": restocked_at,
    }


def get_counts_since_restock():
    """Per-person totals since the last restock, mirroring the Chromebook's
    reset behavior so both dashboards stay in sync."""
    conn = get_db()
    restock_row = conn.execute(
        "SELECT timestamp FROM restocks ORDER BY id DESC LIMIT 1"
    ).fetchone()
    restocked_at = restock_row["timestamp"] if restock_row else "1970-01-01T00:00:00"

    rows = conn.execute(
        "SELECT name, COUNT(*) as count FROM beers WHERE timestamp > ? "
        "GROUP BY name ORDER BY count DESC",
        (restocked_at,),
    ).fetchall()
    conn.close()
    return {row["name"]: row["count"] for row in rows}


@app.route("/receive", methods=["POST"])
def receive():
    data = request.get_json()
    if not data or "name" not in data or "timestamp" not in data:
        return jsonify({"error": "invalid payload"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO beers (name, timestamp) VALUES (?, ?)",
        (data["name"], data["timestamp"]),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok"}), 200


@app.route("/totals")
def totals():
    """Quick way to check totals: visit http://<pc-ip>:6000/totals in a browser.
    Shows counts since the last restock, same as the Chromebook."""
    return jsonify(get_counts_since_restock())


@app.route("/receive_restock", methods=["POST"])
def receive_restock():
    """Called by the Chromebook whenever the admin restocks there, so this
    backup dashboard reflects the same 'fresh start' point."""
    data = request.get_json()
    if not data or "amount" not in data or "timestamp" not in data:
        return jsonify({"error": "invalid payload"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO restocks (amount, timestamp) VALUES (?, ?)",
        (data["amount"], data["timestamp"]),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "ok"}), 200


@app.route("/admin")
def admin():
    """Simple admin dashboard: visit http://<pc-ip>:6000/admin in a browser."""
    stock = get_stock_stats()
    totals_dict = get_counts_since_restock()
    return render_template("admin.html", stock=stock, totals=totals_dict)


@app.route("/admin/restock", methods=["POST"])
def admin_restock():
    """Lets the admin restock from the PC dashboard. Forwards the action to
    the Chromebook (source of truth) and also logs it locally as a backup."""
    password = request.form.get("password", "")
    amount_raw = request.form.get("amount", "")

    if password != ADMIN_PASSWORD:
        return "Wrong password.", 403

    try:
        amount = int(amount_raw)
    except ValueError:
        return "Invalid amount.", 400

    if amount < 0:
        return "Amount must be positive.", 400

    timestamp = datetime.now().isoformat()

    forwarded = False
    try:
        resp = requests.post(
            CHROMEBOOK_URL,
            json={"password": password, "amount": amount},
            timeout=3,
        )
        forwarded = resp.status_code == 200
    except requests.exceptions.RequestException:
        pass

    # Log locally regardless, so the backup dashboard is correct even if
    # the Chromebook couldn't be reached right now.
    conn = get_db()
    conn.execute(
        "INSERT INTO restocks (amount, timestamp) VALUES (?, ?)",
        (amount, timestamp),
    )
    conn.commit()
    conn.close()

    if not forwarded:
        return (
            "Restock saved here, but the Chromebook could not be reached "
            "(it may be offline). It will show outdated stock numbers "
            "until it's back online and this is retried.",
            200,
        )

    return "Restock recorded and synced to the Chromebook.", 200


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)