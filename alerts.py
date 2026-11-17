from config import *


def new_state():
    return {
        "cpu_streak": 0,
        "net_streak": 0,
        "mem_counter": 0,
        "mem_history": [],
        "seen_watched": set(),
        "zombie_since": {},
    }


def is_open(conn, name):
    row = conn.execute(
        "SELECT 1 FROM alerts WHERE name = ? AND status = 'open' LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def open_alert(conn, name, ts, peak):
    conn.execute(
        "INSERT INTO alerts (name, opened_ts, closed_ts, peak, status)"
        " VALUES (?, ?, NULL, ?, 'open')",
        (name, ts, peak),
    )


def close_alert(conn, name, ts):
    conn.execute(
        "UPDATE alerts SET closed_ts = ?, status = 'closed'"
        " WHERE name = ? AND status = 'open'",
        (ts, name),
    )


def update_peak(conn, name, peak):
    conn.execute(
        "UPDATE alerts SET peak = MAX(peak, ?) WHERE name = ? AND status = 'open'",
        (peak, name),
    )


def check_all(conn, ts, cpu_total, mem, disk, probe, procs, watched, state):
    if cpu_total > CPU_HIGH_THRESHOLD:
        state["cpu_streak"] += 1
    else:
        state["cpu_streak"] = 0
    if state["cpu_streak"] >= CPU_HIGH_SAMPLES:
        if not is_open(conn, "cpu_high"):
            open_alert(conn, "cpu_high", ts, cpu_total)
        else:
            update_peak(conn, "cpu_high", cpu_total)
    elif is_open(conn, "cpu_high"):
        close_alert(conn, "cpu_high", ts)

    if mem.percent > RAM_HIGH_THRESHOLD:
        if not is_open(conn, "ram_high"):
            open_alert(conn, "ram_high", ts, mem.percent)
        else:
            update_peak(conn, "ram_high", mem.percent)
    elif is_open(conn, "ram_high"):
        close_alert(conn, "ram_high", ts)

    if disk.percent > DISK_HIGH_THRESHOLD:
        if not is_open(conn, "disk_high"):
            open_alert(conn, "disk_high", ts, disk.percent)
        else:
            update_peak(conn, "disk_high", disk.percent)
    elif is_open(conn, "disk_high"):
        close_alert(conn, "disk_high", ts)

    if probe is not None:
        if probe["gateway_ok"] == 0:
            state["net_streak"] += 1
        else:
            state["net_streak"] = 0
        if state["net_streak"] >= NET_LOST_ROUNDS:
            if not is_open(conn, "network_lost"):
                open_alert(conn, "network_lost", ts, state["net_streak"])
            else:
                update_peak(conn, "network_lost", state["net_streak"])
        elif is_open(conn, "network_lost"):
            close_alert(conn, "network_lost", ts)

    state["mem_counter"] += 1
    if state["mem_counter"] >= 12:
        state["mem_counter"] = 0
        state["mem_history"].append(mem.used // 1024 // 1024)
        state["mem_history"] = state["mem_history"][-MEM_CLIMB_MINUTES:]
        hist = state["mem_history"]
        if len(hist) == MEM_CLIMB_MINUTES:
            rises = sum(1 for a, b in zip(hist, hist[1:]) if b > a)
            climbed = hist[-1] - hist[0]
            if rises >= MEM_CLIMB_RISES and climbed >= MEM_CLIMB_MB:
                if not is_open(conn, "memory_climbing"):
                    open_alert(conn, "memory_climbing", ts, climbed)
                else:
                    update_peak(conn, "memory_climbing", climbed)
            elif is_open(conn, "memory_climbing"):
                close_alert(conn, "memory_climbing", ts)

    names = {info["name"] for _, _, info in procs}
    for name in watched:
        alert_name = f"process_died:{name}"
        if name in names:
            state["seen_watched"].add(name)
            if is_open(conn, alert_name):
                close_alert(conn, alert_name, ts)
        elif name in state["seen_watched"]:
            if not is_open(conn, alert_name):
                open_alert(conn, alert_name, ts, 1.0)

    now_zombies = {}
    for _, _, info in procs:
        if info.get("status") == "zombie":
            now_zombies[info["pid"]] = info["name"]
    for pid in now_zombies:
        first = state["zombie_since"].setdefault(pid, ts)
        if ts - first >= ZOMBIE_SECONDS:
            alert_name = f"zombie:{pid}"
            if not is_open(conn, alert_name):
                open_alert(conn, alert_name, ts, ts - first)
            else:
                update_peak(conn, alert_name, ts - first)
    for pid in list(state["zombie_since"]):
        if pid not in now_zombies:
            alert_name = f"zombie:{pid}"
            if is_open(conn, alert_name):
                close_alert(conn, alert_name, ts)
            del state["zombie_since"][pid]

    live_pids = {info["pid"] for _, _, info in procs}
    for cpu_pct, rss, info in procs:
        pid = info["pid"]
        rss_mb = rss // 1024 // 1024
        if cpu_pct > PROC_CPU_THRESHOLD:
            name = f"proc_cpu_high:{pid}"
            if not is_open(conn, name):
                open_alert(conn, name, ts, cpu_pct)
            else:
                update_peak(conn, name, cpu_pct)
        elif is_open(conn, f"proc_cpu_high:{pid}"):
            close_alert(conn, f"proc_cpu_high:{pid}", ts)
        if rss_mb > PROC_RAM_MB:
            name = f"proc_ram_high:{pid}"
            if not is_open(conn, name):
                open_alert(conn, name, ts, rss_mb)
            else:
                update_peak(conn, name, rss_mb)
        elif is_open(conn, f"proc_ram_high:{pid}"):
            close_alert(conn, f"proc_ram_high:{pid}", ts)
    rows = conn.execute("SELECT name FROM alerts WHERE status = 'open' AND (name LIKE 'proc_cpu_high:%' OR name LIKE 'proc_ram_high:%')").fetchall()
    for row in rows:
        pid = int(row[0].split(":")[1])
        if pid not in live_pids:
            close_alert(conn, row[0], ts)
