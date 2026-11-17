import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import config

CRITICAL_ALERTS = {"cpu_high", "ram_high", "disk_high", "network_lost"}


def get_db():
    conn = sqlite3.connect(config.DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def read_json(handler, data, status=200):
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            self.serve_file("dashboard.html", "text/html")
        elif path == "/health":
            self.health()
        elif path == "/metrics":
            self.metrics()
        elif path == "/history":
            hours = int(query.get("hours", ["1"])[0])
            self.history(hours)
        elif path == "/alerts":
            self.alerts()
        elif path == "/processes":
            self.processes()
        else:
            read_json(self, {"error": "not found"}, 404)

    def serve_file(self, name, content_type):
        try:
            with open(name, "rb") as f:
                body = f.read()
        except FileNotFoundError:
            read_json(self, {"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def health(self):
        conn = get_db()
        rows = conn.execute("SELECT name FROM alerts WHERE status = 'open'").fetchall()
        conn.close()
        names = [r["name"] for r in rows]
        if not names:
            status = "ok"
        elif any(n in CRITICAL_ALERTS for n in names):
            status = "critical"
        else:
            status = "warn"
        read_json(self, {"status": status, "open_alerts": names})

    def metrics(self):
        conn = get_db()
        rows = conn.execute("SELECT * FROM system_samples ORDER BY ts DESC LIMIT 2").fetchall()
        probe = conn.execute("SELECT * FROM probes ORDER BY ts DESC LIMIT 1").fetchone()
        conn.close()
        data = dict(rows[0]) if rows else {}
        if len(rows) == 2:
            dt = rows[0]["ts"] - rows[1]["ts"]
            if dt > 0:
                data["net_sent_rate"] = (rows[0]["net_sent"] - rows[1]["net_sent"]) / dt
                data["net_recv_rate"] = (rows[0]["net_recv"] - rows[1]["net_recv"]) / dt
        if probe:
            data["probe"] = dict(probe)
        read_json(self, data)


    def history(self, hours):
        conn = get_db()
        since = int(time.time()) - hours * 3600
        rows = conn.execute(
            "SELECT ts, cpu_total, ram_percent, disk_percent FROM system_samples WHERE ts >= ? ORDER BY ts",
            (since,),
        ).fetchall()
        conn.close()
        read_json(self, [dict(r) for r in rows])

    def alerts(self):
        conn = get_db()
        open_rows = conn.execute("SELECT * FROM alerts WHERE status = 'open' ORDER BY opened_ts").fetchall()
        closed_rows = conn.execute("SELECT * FROM alerts WHERE status = 'closed' ORDER BY closed_ts DESC LIMIT 20").fetchall()
        conn.close()
        read_json(self, {"open": [dict(r) for r in open_rows], "closed": [dict(r) for r in closed_rows]})

    def processes(self):
        conn = get_db()
        row = conn.execute("SELECT MAX(ts) AS ts FROM process_samples").fetchone()
        if row is None or row["ts"] is None:
            conn.close()
            read_json(self, [])
            return
        rows = conn.execute(
            "SELECT pid, name, cpu, rss FROM process_samples WHERE ts = ? ORDER BY cpu DESC LIMIT 10",
            (row["ts"],),
        ).fetchall()
        conn.close()
        read_json(self, [dict(r) for r in rows])


def run():
    server = ThreadingHTTPServer(("0.0.0.0", config.API_PORT), Handler)
    server.serve_forever()