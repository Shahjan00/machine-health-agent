import os
import threading
import time

import psutil

import alerts
import api
import db
import probes
from config import WATCHED

probe_lock = threading.Lock()
probe_state = {"result": None, "ts": 0}


def probe_worker():
    while True:
        try:
            result = probes.run_all()
        except Exception:
            result = None
        with probe_lock:
            probe_state["result"] = result
            probe_state["ts"] = int(time.time())
        time.sleep(15)


SKIP_FSTYPES = {
    "proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
    "securityfs", "pstore", "efivarfs", "debugfs", "tracefs",
    "configfs", "fusectl", "mqueue", "hugetlbfs", "autofs",
    "binfmt_misc", "squashfs", "iso9660",
}


def sample_mounts():
    mounts = []
    seen = set()
    seen_mounts = set()
    try:
        parts = psutil.disk_partitions(all=True)
    except Exception:
        return mounts
    for part in sorted(parts, key=lambda p: len(p.mountpoint)):
        if part.fstype in SKIP_FSTYPES:
            continue
        if part.mountpoint in ("/sys", "/proc") or part.mountpoint.startswith(("/sys/", "/proc/")):
            continue
        if os.path.isfile(part.mountpoint):
            continue
        if part.mountpoint in seen_mounts:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        if usage.total <= 0:
            continue
        key = (part.device, usage.total)
        if key in seen:
            continue
        seen.add(key)
        seen_mounts.add(part.mountpoint)
        mounts.append((part.mountpoint, usage.percent))
    return mounts


def main():
    conn = db.connect()
    state = alerts.new_state()

    for p in psutil.process_iter():
        try:
            p.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    threading.Thread(target=api.run, daemon=True).start()
    threading.Thread(target=probe_worker, daemon=True).start()

    last_probe_ts = 0
    while True:
        try:
            ts = int(time.time())
            cpu = psutil.cpu_percent(percpu=True)
            mem = psutil.virtual_memory()
            mounts = sample_mounts()
            disk_max = max([percent for _, percent in mounts], default=0.0)
            net = psutil.net_io_counters()
            load = os.getloadavg()
            cpu_total = sum(cpu) / len(cpu)

            procs = []
            for p in psutil.process_iter(["pid", "name", "status"]):
                try:
                    procs.append((p.cpu_percent(), p.memory_info().rss, p.info))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            procs.sort(key=lambda t: t[0], reverse=True)

            save = procs[:10]
            saved_names = {info["name"] for _, _, info in save}
            for item in procs:
                if item[2]["name"] in WATCHED and item[2]["name"] not in saved_names:
                    save.append(item)

            db.save_system(conn, ts, cpu_total, mem.percent, disk_max,
                           net.bytes_sent, net.bytes_recv, int(ts - psutil.boot_time()), load)
            db.save_processes(conn, ts, save)
            db.save_disk(conn, ts, mounts)
            db.prune(conn, ts)
            conn.commit()

            probe_result = None
            probe_ts = 0
            with probe_lock:
                if probe_state["ts"] != last_probe_ts:
                    last_probe_ts = probe_state["ts"]
                    probe_result = probe_state["result"]
                    probe_ts = probe_state["ts"]
            if probe_result is not None:
                db.save_probe(conn, probe_ts, probe_result["dns_ms"], probe_result["tcp_ms"],
                            probe_result["ping_gateway_loss"], probe_result["ping_internet_loss"],
                            probe_result["gateway_ok"], probe_result["api_status"], probe_result["api_ms"])
                conn.commit()
                print(f"Probe: dns={probe_result['dns_ms']}ms tcp={probe_result['tcp_ms']}ms "
                    f"gw_loss={probe_result['ping_gateway_loss']}% net_loss={probe_result['ping_internet_loss']}%")

            alerts.check_all(conn, ts, cpu_total, mem, mounts, probe_result, procs, WATCHED, state)
            conn.commit()
            open_now = conn.execute("SELECT name, peak FROM alerts WHERE status = 'open'").fetchall()
            if open_now:
                print("Open alerts:", open_now)

            print(f"CPU total: {cpu_total:.2f}%")
            print(f"CPU per core: {[round(c, 1) for c in cpu]}")
            print(f"RAM: {mem.percent}% used, {mem.used//1024//1024} MB")
            print(f"Disk max: {disk_max}% used ({len(mounts)} mounts)")
            print(f"Net: sent {net.bytes_sent} recv {net.bytes_recv}")
            print(f"Uptime: {int(ts - psutil.boot_time())}s")
            print(f"Load: {load}")
            print("Top processes by CPU:")
            for cpu_pct, rss, info in save[:5]:
                print(f"  {info['pid']:>6} {info['name']} cpu={cpu_pct:.1f}% rss={rss//1024//1024} MB")
            print("=" * 60)
        except Exception as err:
            print(f"sample failed: {err}")
        time.sleep(5)


if __name__ == "__main__":
    main()
