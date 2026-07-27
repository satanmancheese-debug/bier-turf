"""
Beer Stripe Tracker - Flask app
Runs locally on the touchscreen device (Chromebook).
Stores taps in a local SQLite database and tries to sync each one
to your main PC over HTTP. If the PC is unreachable, the tap is
still saved locally and marked as "unsynced" so a background job
can retry later.
"""

from flask import Flask, render_template, jsonify, request
import sqlite3
import requests
import os
from datetime import datetime, timedelta

app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "beers.db")

# --- EDIT THESE FOR your setup ---
PC_SYNC_URL = "http://192.168.1.50:6000/receive"  # your PC's local IP + port
HOUSEMATES = ["Arlo", "Deven", "Nienk", "Piet", "Eva", "Floris"]  # edit to your housemates
ADMIN_PASSWORD = "changeme"  # change this! Must match the same value in pc-receiver
# ----------------------------------


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
            synced INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            bought INTEGER NOT NULL DEFAULT 0,
            restocked_at TEXT NOT NULL
        )
    """)
    # Make sure the single stock row exists so UPDATE always has something to hit
    existing = conn.execute("SELECT id FROM stock WHERE id = 1").fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO stock (id, bought, restocked_at) VALUES (1, 0, ?)",
            (datetime.now().isoformat(),),
        )
    conn.commit()
    conn.close()


def get_counts():
    """Per-person counts since the last restock (so the leaderboard/crown/
    jester reset along with the stock numbers whenever the admin restocks)."""
    conn = get_db()
    restocked_at = conn.execute(
        "SELECT restocked_at FROM stock WHERE id = 1"
    ).fetchone()["restocked_at"]
    rows = conn.execute(
        "SELECT name, COUNT(*) as count FROM beers WHERE timestamp > ? GROUP BY name",
        (restocked_at,),
    ).fetchall()
    conn.close()
    counts = {name: 0 for name in HOUSEMATES}
    for row in rows:
        counts[row["name"]] = row["count"]
    return counts


def get_stock_stats():
    """
    available = bought - drunk since the last restock
    drunk     = beers tapped since the last restock timestamp
    pace_24h  = beers tapped in the last rolling 24 hours (regardless of restocks)
    """
    conn = get_db()
    stock_row = conn.execute(
        "SELECT bought, restocked_at FROM stock WHERE id = 1"
    ).fetchone()
    bought = stock_row["bought"]
    restocked_at = stock_row["restocked_at"]

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


def try_sync(entry_id, name, timestamp):
    """Best-effort push to the PC. Never blocks the UI for long."""
    try:
        resp = requests.post(
            PC_SYNC_URL,
            json={"name": name, "timestamp": timestamp},
            timeout=2,
        )
        if resp.status_code == 200:
            conn = get_db()
            conn.execute("UPDATE beers SET synced = 1 WHERE id = ?", (entry_id,))
            conn.commit()
            conn.close()
            return True
    except requests.exceptions.RequestException:
        pass  # PC offline or unreachable - stays unsynced, retried later
    return False


@app.route("/")
def index():
    counts = get_counts()
    stock = get_stock_stats()
    return render_template("index.html", housemates=HOUSEMATES, counts=counts, stock=stock)


@app.route("/api/counts")
def api_counts():
    return jsonify(get_counts())


@app.route("/api/stock")
def api_stock():
    return jsonify(get_stock_stats())


@app.route("/api/restock", methods=["POST"])
def api_restock():
    """Admin action: wipes the stock counters and sets a new bought amount.
    Does NOT touch each housemate's all-time tap history (crown/jester stats)."""
    data = request.get_json() or {}

    if data.get("password") != ADMIN_PASSWORD:
        return jsonify({"error": "wrong password"}), 403

    try:
        amount = int(data.get("amount"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400

    if amount < 0:
        return jsonify({"error": "amount must be positive"}), 400

    timestamp = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        "UPDATE stock SET bought = ?, restocked_at = ? WHERE id = 1",
        (amount, timestamp),
    )
    conn.commit()
    conn.close()

    # Best-effort: tell the PC about the restock too, so its backup dashboard
    # shows the same "fresh start" point. Never blocks the admin action.
    try:
        restock_url = PC_SYNC_URL.replace("/receive", "/receive_restock")
        requests.post(
            restock_url,
            json={"amount": amount, "timestamp": timestamp},
            timeout=2,
        )
    except requests.exceptions.RequestException:
        pass

    return jsonify(get_stock_stats())


@app.route("/add/<name>", methods=["POST"])
def add_beer(name):
    if name not in HOUSEMATES:
        return jsonify({"error": "unknown housemate"}), 400

    timestamp = datetime.now().isoformat()
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO beers (name, timestamp, synced) VALUES (?, ?, 0)",
        (name, timestamp),
    )
    entry_id = cur.lastrowid
    conn.commit()
    conn.close()

    try_sync(entry_id, name, timestamp)

    return jsonify(get_counts())


@app.route("/api/resync", methods=["POST"])
def resync():
    """Retry sending any taps that never made it to the PC."""
    conn = get_db()
    unsynced = conn.execute(
        "SELECT id, name, timestamp FROM beers WHERE synced = 0"
    ).fetchall()
    conn.close()

    synced_count = 0
    for row in unsynced:
        if try_sync(row["id"], row["name"], row["timestamp"]):
            synced_count += 1

    return jsonify({"synced": synced_count, "remaining": len(unsynced) - synced_count})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)