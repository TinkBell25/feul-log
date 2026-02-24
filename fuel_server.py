"""
Fuel Logger Backend — Full Edition
Run with: python -m pip install flask flask-cors && python fuel_server.py
Then open fuel_logger.html in your browser.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from collections import defaultdict
import sqlite3

app = Flask(__name__)
CORS(app)

DB_PATH = "fuel_log.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cars (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                registration    TEXT NOT NULL UNIQUE,
                description     TEXT NOT NULL,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fuel_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                car_id          INTEGER NOT NULL,
                logged_at       TEXT NOT NULL,
                fuel_amount     REAL NOT NULL,
                fuel_unit       TEXT NOT NULL DEFAULT 'litres',
                price_per_unit  REAL NOT NULL,
                total_cost      REAL NOT NULL,
                odometer        REAL,
                notes           TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (car_id) REFERENCES cars(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS service_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                car_id          INTEGER NOT NULL,
                category        TEXT NOT NULL,
                logged_at       TEXT NOT NULL,
                cost            REAL NOT NULL,
                provider        TEXT,
                notes           TEXT,
                next_due_date   TEXT,
                next_due_km     REAL,
                odometer        REAL,
                created_at      TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (car_id) REFERENCES cars(id)
            )
        """)
        conn.commit()

# ── CARS ──────────────────────────────────────────────────────────────────────

@app.route("/api/cars", methods=["GET"])
def get_cars():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM cars ORDER BY registration").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/cars", methods=["POST"])
def add_car():
    data = request.get_json()
    if not data.get("registration") or not data.get("description"):
        return jsonify({"error": "registration and description are required"}), 400
    reg = data["registration"].strip().upper()
    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO cars (registration, description) VALUES (?, ?)",
                (reg, data["description"].strip())
            )
            conn.commit()
            return jsonify({"id": cursor.lastrowid, "registration": reg}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Registration '{reg}' already exists"}), 409

@app.route("/api/cars/<int:car_id>", methods=["DELETE"])
def delete_car(car_id):
    with get_db() as conn:
        conn.execute("DELETE FROM cars WHERE id = ?", (car_id,))
        conn.commit()
    return jsonify({"deleted": car_id})

# ── FUEL LOGS ─────────────────────────────────────────────────────────────────

@app.route("/api/logs", methods=["GET"])
def get_logs():
    car_id = request.args.get("car_id")
    with get_db() as conn:
        if car_id:
            rows = conn.execute("""
                SELECT fl.*, c.registration, c.description as car_description
                FROM fuel_logs fl JOIN cars c ON fl.car_id = c.id
                WHERE fl.car_id = ?
                ORDER BY fl.logged_at ASC
            """, (car_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT fl.*, c.registration, c.description as car_description
                FROM fuel_logs fl JOIN cars c ON fl.car_id = c.id
                ORDER BY fl.logged_at ASC
            """).fetchall()

    logs = [dict(r) for r in rows]

    # Group by car and compute per-trip consumption
    by_car = defaultdict(list)
    for log in logs:
        by_car[log["car_id"]].append(log)

    for car_logs in by_car.values():
        with_odo = [l for l in car_logs if l["odometer"] is not None]
        with_odo.sort(key=lambda x: x["logged_at"])
        for i, log in enumerate(with_odo):
            if i == 0:
                log["distance_km"]        = None
                log["consumption_per100"] = None
                log["rand_per_km"]        = None
                log["rand_per_litre_trip"] = None
            else:
                prev = with_odo[i - 1]
                dist = log["odometer"] - prev["odometer"]
                if dist > 0:
                    log["distance_km"]         = round(dist, 1)
                    log["consumption_per100"]  = round((log["fuel_amount"] / dist) * 100, 2)
                    log["rand_per_km"]         = round(log["total_cost"] / dist, 4)
                    log["rand_per_litre_trip"] = round(log["price_per_unit"], 4)
                else:
                    log["distance_km"]         = None
                    log["consumption_per100"]  = None
                    log["rand_per_km"]         = None
                    log["rand_per_litre_trip"] = None

    logs.sort(key=lambda x: x["logged_at"], reverse=True)
    return jsonify(logs)

@app.route("/api/logs", methods=["POST"])
def add_log():
    data = request.get_json()
    for field in ["car_id", "logged_at", "fuel_amount", "fuel_unit", "price_per_unit"]:
        if not data.get(field):
            return jsonify({"error": f"Missing field: {field}"}), 400

    total_cost = round(float(data["fuel_amount"]) * float(data["price_per_unit"]), 2)
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO fuel_logs
                (car_id, logged_at, fuel_amount, fuel_unit, price_per_unit, total_cost, odometer, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["car_id"], data["logged_at"],
            float(data["fuel_amount"]), data["fuel_unit"],
            float(data["price_per_unit"]), total_cost,
            float(data["odometer"]) if data.get("odometer") else None,
            data.get("notes", "")
        ))
        conn.commit()
    return jsonify({"id": cursor.lastrowid, "total_cost": total_cost}), 201

@app.route("/api/logs/<int:log_id>", methods=["DELETE"])
def delete_log(log_id):
    with get_db() as conn:
        conn.execute("DELETE FROM fuel_logs WHERE id = ?", (log_id,))
        conn.commit()
    return jsonify({"deleted": log_id})

# ── STATS ─────────────────────────────────────────────────────────────────────

@app.route("/api/stats", methods=["GET"])
def get_stats():
    car_id = request.args.get("car_id")
    where  = "WHERE car_id = ?" if car_id else ""
    args   = (car_id,) if car_id else ()
    with get_db() as conn:
        base = conn.execute(f"""
            SELECT
                COUNT(*)                         AS total_entries,
                COALESCE(SUM(fuel_amount),  0)   AS total_fuel,
                COALESCE(SUM(total_cost),   0)   AS total_spent,
                COALESCE(AVG(price_per_unit),0)  AS avg_price_per_unit,
                MIN(odometer)                    AS first_odo,
                MAX(odometer)                    AS last_odo
            FROM fuel_logs {where}
        """, args).fetchone()
        stats = dict(base)

        first_odo, last_odo = stats["first_odo"], stats["last_odo"]
        if first_odo is not None and last_odo is not None and last_odo > first_odo:
            dist = last_odo - first_odo
            stats["total_distance_km"]      = round(dist, 1)
            stats["avg_consumption_per100"] = round((stats["total_fuel"] / dist) * 100, 2)
            stats["overall_rand_per_km"]    = round(stats["total_spent"] / dist, 4)
        else:
            stats["total_distance_km"]      = None
            stats["avg_consumption_per100"] = None
            stats["overall_rand_per_km"]    = None

        stats["overall_rand_per_litre"] = (
            round(stats["total_spent"] / stats["total_fuel"], 4)
            if stats["total_fuel"] > 0 else None
        )

        svc = conn.execute(f"""
            SELECT category, COALESCE(SUM(cost),0) AS total, COUNT(*) AS count
            FROM service_logs {where}
            GROUP BY category
        """, args).fetchall()
        stats["service_breakdown"] = [dict(r) for r in svc]
        stats["total_service_cost"] = sum(r["total"] for r in svc)

    return jsonify(stats)

# ── SERVICE LOGS ──────────────────────────────────────────────────────────────

VALID_CATEGORIES = {"tyres", "car_wash", "car_service", "panel_beating", "special_service"}

@app.route("/api/services", methods=["GET"])
def get_services():
    car_id   = request.args.get("car_id")
    category = request.args.get("category")
    conditions, args = [], []
    if car_id:   conditions.append("sl.car_id = ?");   args.append(car_id)
    if category: conditions.append("sl.category = ?"); args.append(category)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT sl.*, c.registration, c.description as car_description
            FROM service_logs sl JOIN cars c ON sl.car_id = c.id
            {where}
            ORDER BY sl.logged_at DESC
        """, args).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/services", methods=["POST"])
def add_service():
    data = request.get_json()
    for field in ["car_id", "category", "logged_at", "cost"]:
        if data.get(field) is None:
            return jsonify({"error": f"Missing field: {field}"}), 400
    if data["category"] not in VALID_CATEGORIES:
        return jsonify({"error": f"Invalid category"}), 400
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO service_logs
                (car_id, category, logged_at, cost, provider, notes, next_due_date, next_due_km, odometer)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["car_id"], data["category"], data["logged_at"], float(data["cost"]),
            data.get("provider", ""), data.get("notes", ""),
            data.get("next_due_date") or None,
            float(data["next_due_km"]) if data.get("next_due_km") else None,
            float(data["odometer"])    if data.get("odometer")    else None,
        ))
        conn.commit()
    return jsonify({"id": cursor.lastrowid}), 201

@app.route("/api/services/<int:svc_id>", methods=["DELETE"])
def delete_service(svc_id):
    with get_db() as conn:
        conn.execute("DELETE FROM service_logs WHERE id = ?", (svc_id,))
        conn.commit()
    return jsonify({"deleted": svc_id})

if __name__ == "__main__":
    init_db()
    print("✅  Fuel Logger running at http://localhost:5000")
    print("📂  Database: fuel_log.db")
    app.run(debug=True, port=5000)
