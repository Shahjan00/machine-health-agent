from config import *


def new_state():
    return {
        "cpu_first": None,
        "net_streak": 0,
        "mem_counter": 0,
        "mem_proc_history": {},
        "seen_watched": {},
        "first_seen": {},
        "zombie_since": {},
        "proc_cpu_streak": {},
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


def check_all(conn, ts, cpu_total, mem, mounts, probe, procs, watched, state):
    if cpu_total > CPU_HIGH_THRESHOLD:
        if state["cpu_first"] is None:
            state["cpu_first"] = ts
        if ts - state["cpu_first"] >= 60:
            if not is_open(conn, "cpu_high"):
                open_alert(conn, "cpu_high", ts, cpu_total)
            else:
                update_peak(conn, "cpu_high", cpu_total)
    else:
        state["cpu_first"] = None
        if is_open(conn, "cpu_high"):
            close_alert(conn, "cpu_high", ts)

    if mem.percent > RAM_HIGH_THRESHOLD:
        if not is_open(conn, "ram_high"):
            open_alert(conn, "ram_high", ts, mem.percent)
        else:
            update_peak(conn, "ram_high", mem.percent)
    elif is_open(conn, "ram_high"):
        close_alert(conn, "ram_high", ts)

    for mount, percent in mounts:
        alert_name = f"disk_high:{mount}"
        if percent > DISK_HIGH_THRESHOLD:
            if not is_open(conn, alert_name):
                open_alert(conn, alert_name, ts, percent)
            else:
                update_peak(conn, alert_name, percent)
        elif is_open(conn, alert_name):
            close_alert(conn, alert_name, ts)

    if probe is not None:
        failed = probe["gateway_ok"] == 0 or probe["ping_internet_loss"] == 100.0
        if failed:
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
        live_pids = {info["pid"] for _, _, info in procs}
        for _, rss, info in procs:
            pid = info["pid"]
            hist = state["mem_proc_history"].setdefault(pid, [])
            hist.append(rss // 1024 // 1024)
            state["mem_proc_history"][pid] = hist[-MEM_CLIMB_MINUTES:]
        for pid in list(state["mem_proc_history"]):
            hist = state["mem_proc_history"][pid]
            alert_name = f"memory_climbing:{pid}"
            if len(hist) == MEM_CLIMB_MINUTES:
                rises = sum(1 for a, b in zip(hist, hist[1:]) if b > a)
                climbed = hist[-1] - hist[0]
                if rises >= MEM_CLIMB_RISES and climbed >= MEM_CLIMB_MB:
                    if not is_open(conn, alert_name):
                        open_alert(conn, alert_name, ts, climbed)
                    else:
                        update_peak(conn, alert_name, climbed)
                elif is_open(conn, alert_name):
                    close_alert(conn, alert_name, ts)
            if pid not in live_pids:
                if is_open(conn, alert_name):
                    close_alert(conn, alert_name, ts)
                del state["mem_proc_history"][pid]

    for name in watched:
        current = {info["pid"] for _, _, info in procs if info["name"] == name}
        if current:
            rows = conn.execute("SELECT name FROM alerts WHERE status = 'open' AND name LIKE ?", (f"process_died:{name}:%",)).fetchall()
            for row in rows:
                close_alert(conn, row[0], ts)
        seen = state["seen_watched"].setdefault(name, set())
        first_seen = state["first_seen"].setdefault(name, {})
        for pid in list(seen - current):
            alert_name = f"process_died:{name}:{pid}"
            if not is_open(conn, alert_name):
                open_alert(conn, alert_name, ts, 1.0)
            seen.discard(pid)
        for pid in current:
            if pid not in first_seen:
                first_seen[pid] = ts
            elif ts - first_seen[pid] >= 5 and pid not in seen:
                seen.add(pid)
        for pid in list(first_seen):
            if pid not in current:
                del first_seen[pid]

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
            streak = state["proc_cpu_streak"].get(pid, 0) + 1
            state["proc_cpu_streak"][pid] = streak
            if streak >= PROC_CPU_SAMPLES:
                name = f"proc_cpu_high:{pid}"
                if not is_open(conn, name):
                    open_alert(conn, name, ts, cpu_pct)
                else:
                    update_peak(conn, name, cpu_pct)
        else:
            state["proc_cpu_streak"][pid] = 0
            if is_open(conn, f"proc_cpu_high:{pid}"):
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
            state["proc_cpu_streak"].pop(pid, None)
