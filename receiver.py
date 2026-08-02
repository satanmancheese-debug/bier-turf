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
CHROMEBOOK_URL = "http://127.0.0.1:5000/api/restock" # Chromebook's WireGuard IP
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
            cost REAL NOT NULL DEFAULT 0,
            timestamp TEXT NOT NULL,
            received_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migration: add the cost column if this is an older database that
    # doesn't have it yet (won't error on a fresh database that already has it)
    existing_columns = [row["name"] for row in conn.execute("PRAGMA table_info(restocks)")]
    if "cost" not in existing_columns:
        conn.execute("ALTER TABLE restocks ADD COLUMN cost REAL NOT NULL DEFAULT 0")
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


def _format_label(iso_timestamp):
    """'2026-07-26T18:33:40' -> '26 Jul'"""
    try:
        return datetime.fromisoformat(iso_timestamp).strftime("%d %b")
    except (ValueError, TypeError):
        return iso_timestamp


def get_restock_history():
    """All restocks, oldest first, each with a derived name (start - end date),
    its price per beer, and its end timestamp (the next restock's start, or
    None if it's still the current/ongoing one)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, amount, cost, timestamp FROM restocks ORDER BY id ASC"
    ).fetchall()
    conn.close()

    history = []
    for i, row in enumerate(rows):
        start = row["timestamp"]
        end = rows[i + 1]["timestamp"] if i + 1 < len(rows) else None
        price_per_beer = (row["cost"] / row["amount"]) if row["amount"] else 0

        label = _format_label(start) if end is None else f"{_format_label(start)} - {_format_label(end)}"

        history.append({
            "id": row["id"],
            "label": label,
            "start": start,
            "end": end,  # None means "ongoing"
            "amount": row["amount"],
            "cost": row["cost"],
            "price_per_beer": price_per_beer,
        })

    return history


def get_breakdown_for_period(start, end):
    """Per-person tap counts between two timestamps. end=None means 'now'."""
    conn = get_db()
    if end is None:
        rows = conn.execute(
            "SELECT name, COUNT(*) as count FROM beers WHERE timestamp > ? "
            "GROUP BY name ORDER BY count DESC",
            (start,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT name, COUNT(*) as count FROM beers WHERE timestamp > ? AND timestamp <= ? "
            "GROUP BY name ORDER BY count DESC",
            (start, end),
        ).fetchall()
    conn.close()
    return {row["name"]: row["count"] for row in rows}


def get_alltime_stats():
    """Lifetime totals per person: beers drunk and total cost owed, summed
    across every restock cycle (each cycle priced at its own price/beer)."""
    totals = {}  # name -> {"beers": int, "cost": float}

    for cycle in get_restock_history():
        breakdown = get_breakdown_for_period(cycle["start"], cycle["end"])
        for name, count in breakdown.items():
            entry = totals.setdefault(name, {"beers": 0, "cost": 0.0})
            entry["beers"] += count
            entry["cost"] += count * cycle["price_per_beer"]

    return totals


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
    """Quick way to check totals: visit http://<pc-ip>:8000/totals in a browser.
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
    """Simple admin dashboard: visit http://<pc-ip>:8000/admin in a browser."""
    stock = get_stock_stats()
    totals_dict = get_counts_since_restock()
    return render_template("admin.html", stock=stock, totals=totals_dict)


@app.route("/admin/restock", methods=["POST"])
def admin_restock():
    """Lets the admin restock from the PC dashboard. Forwards the action to
    the Chromebook (source of truth) and also logs it locally as a backup."""
    password = request.form.get("password", "")
    amount_raw = request.form.get("amount", "")
    cost_raw = request.form.get("cost", "0")

    if password != ADMIN_PASSWORD:
        return "Wrong password.", 403

    try:
        amount = int(amount_raw)
        cost = float(cost_raw) if cost_raw else 0.0
    except ValueError:
        return "Invalid amount or cost.", 400

    if amount < 0 or cost < 0:
        return "Amount and cost must be positive.", 400

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
        "INSERT INTO restocks (amount, cost, timestamp) VALUES (?, ?, ?)",
        (amount, cost, timestamp),
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


# --- JSON API for the Tkinter admin GUI (or any other remote client) ---

@app.route("/api/stock")
def api_stock():
    return jsonify(get_stock_stats())


@app.route("/api/counts")
def api_counts():
    return jsonify(get_counts_since_restock())


@app.route("/api/restocks")
def api_restocks():
    """Full restock history: derived name/label, date range, amount, cost,
    and price per beer for each cycle."""
    return jsonify(get_restock_history())


@app.route("/api/restocks/<int:restock_id>/breakdown")
def api_restock_breakdown(restock_id):
    """Per-person tap counts (and their cost) for one specific restock cycle."""
    history = get_restock_history()
    cycle = next((c for c in history if c["id"] == restock_id), None)
    if cycle is None:
        return jsonify({"error": "restock not found"}), 404

    counts = get_breakdown_for_period(cycle["start"], cycle["end"])
    breakdown = {
        name: {"beers": count, "cost": count * cycle["price_per_beer"]}
        for name, count in counts.items()
    }
    return jsonify({"cycle": cycle, "breakdown": breakdown})


@app.route("/api/alltime")
def api_alltime():
    """Lifetime per-person totals (beers + money owed) across every restock."""
    return jsonify(get_alltime_stats())


@app.route("/api/restock", methods=["POST"])
def api_restock():
    """Restock endpoint for the Tkinter GUI (JSON in, JSON out) - does the
    same thing as /admin/restock but designed for a non-browser client."""
    data = request.get_json() or {}
    password = data.get("password", "")

    if password != ADMIN_PASSWORD:
        return jsonify({"error": "wrong password"}), 403

    try:
        amount = int(data.get("amount"))
        cost = float(data.get("cost", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount or cost"}), 400

    if amount < 0 or cost < 0:
        return jsonify({"error": "amount and cost must be positive"}), 400

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

    conn = get_db()
    conn.execute(
        "INSERT INTO restocks (amount, cost, timestamp) VALUES (?, ?, ?)",
        (amount, cost, timestamp),
    )
    conn.commit()
    conn.close()

    return jsonify({
        "status": "ok",
        "forwarded_to_chromebook": forwarded,
        "stock": get_stock_stats(),
    })


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8000, debug=False)