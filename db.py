import sqlite3

DB_PATH = "health.db"
RETENTION_SECONDS = 7 * 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS system_samples (
    ts INTEGER,
    cpu_total REAL,
    ram_percent REAL,
    disk_percent REAL,
    net_sent INTEGER,
    net_recv INTEGER,
    uptime INTEGER,
    load1 REAL,
    load5 REAL,
    load15 REAL
);
CREATE TABLE IF NOT EXISTS process_samples (
    ts INTEGER,
    pid INTEGER,
    name TEXT,
    cpu REAL,
    rss INTEGER
);
CREATE TABLE IF NOT EXISTS probes (
    ts INTEGER,
    dns_ms REAL,
    tcp_ms REAL,
    ping_gateway_loss REAL,
    ping_internet_loss REAL,
    gateway_ok INTEGER,
    api_status INTEGER,
    api_ms REAL
);
CREATE TABLE IF NOT EXISTS disk_samples (
    ts INTEGER,
    mount TEXT,
    percent REAL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    opened_ts INTEGER,
    closed_ts INTEGER,
    peak REAL,
    status TEXT
);
CREATE INDEX IF NOT EXISTS idx_system_ts ON system_samples(ts);
CREATE INDEX IF NOT EXISTS idx_process_ts ON process_samples(ts);
CREATE INDEX IF NOT EXISTS idx_probes_ts ON probes(ts);
CREATE INDEX IF NOT EXISTS idx_disk_ts ON disk_samples(ts);

"""

def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

def save_system(conn, ts, cpu_total, ram_percent, disk_percent,
                net_sent, net_recv, uptime, load):
    conn.execute(
        "INSERT INTO system_samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, cpu_total, ram_percent, disk_percent, net_sent, net_recv,
         uptime, load[0], load[1], load[2]),
    )

def save_processes(conn, ts, procs):
    conn.executemany(
        "INSERT INTO process_samples VALUES (?, ?, ?, ?, ?)",
        [(ts, info["pid"], info["name"], cpu, rss) for cpu, rss, info in procs],
    )

def save_disk(conn, ts, mounts):
    conn.executemany(
        "INSERT INTO disk_samples VALUES (?, ?, ?)",
        [(ts, mount, percent) for mount, percent in mounts],
    )

def prune(conn, ts):
    cutoff = ts - RETENTION_SECONDS
    conn.execute("DELETE FROM system_samples WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM process_samples WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM disk_samples WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM probes WHERE ts < ?", (cutoff,))
    conn.execute("DELETE FROM alerts WHERE status = 'closed' AND closed_ts < ?", (cutoff,))

def save_probe(conn, ts, dns_ms, tcp_ms, ping_gateway_loss,
               ping_internet_loss, gateway_ok, api_status, api_ms):
    conn.execute(
        "INSERT INTO probes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, dns_ms, tcp_ms, ping_gateway_loss, ping_internet_loss,
         gateway_ok, api_status, api_ms),
    )
