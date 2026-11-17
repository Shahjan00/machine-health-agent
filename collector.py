import os
import time
import psutil
import probes
import db
import alerts



WATCHED = []  # add process names to watch here

conn = db.connect()
state = alerts.new_state()

for p in psutil.process_iter():
    try:
        p.cpu_percent()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

loop = 0
while True:
    ts = int(time.time())
    cpu = psutil.cpu_percent(percpu=True)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
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

    db.save_system(conn, ts, cpu_total, mem.percent, disk.percent,
                   net.bytes_sent, net.bytes_recv, int(ts - psutil.boot_time()), load)
    db.save_processes(conn, ts, save)
    db.prune(conn, ts)
    conn.commit()

    probe_result = None

    loop += 1
    if loop % 3 == 0:
        result = probes.run_all()
        probe_result = result
        db.save_probe(conn, ts, result["dns_ms"], result["tcp_ms"],
                    result["ping_gateway_loss"], result["ping_internet_loss"],
                    result["gateway_ok"], result["api_status"], result["api_ms"])
        conn.commit()
        print(f"Probe: dns={result['dns_ms']}ms tcp={result['tcp_ms']}ms "
            f"gw_loss={result['ping_gateway_loss']}% net_loss={result['ping_internet_loss']}%")

    alerts.check_all(conn, ts, cpu_total, mem, disk, probe_result, procs, WATCHED, state)
    conn.commit()
    open_now = conn.execute("SELECT name, peak FROM alerts WHERE status = 'open'").fetchall()
    if open_now:
        print("Open alerts:", open_now)

    print(f"CPU total: {cpu_total:.2f}%")
    print(f"CPU per core: {[round(c, 1) for c in cpu]}")
    print(f"RAM: {mem.percent}% used, {mem.used//1024//1024} MB")
    print(f"Disk /: {disk.percent}% used")
    print(f"Net: sent {net.bytes_sent} recv {net.bytes_recv}")
    print(f"Uptime: {int(ts - psutil.boot_time())}s")
    print(f"Load: {load}")
    print("Top processes by CPU:")
    for cpu_pct, rss, info in save[:5]:
        print(f"  {info['pid']:>6} {info['name']} cpu={cpu_pct:.1f}% rss={rss//1024//1024} MB")
    print("=" * 60)
    time.sleep(5)
