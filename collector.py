"""Step 2: read the machine every 5 seconds, print the numbers, save them to SQLite."""
import os
import time
import psutil
import probes
import db

WATCHED = []  # add process names to watch here, e.g. "nginx"

conn = db.connect()

# Prime CPU readings: the first call only sets a baseline, it returns 0.
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
    for p in psutil.process_iter(["pid", "name"]):
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

    loop += 1
    if loop % 3 == 0:
        result = probes.run_all()
        db.save_probe(conn, ts, result["dns_ms"], result["tcp_ms"],
                    result["ping_gateway_loss"], result["ping_internet_loss"],
                    result["gateway_ok"], result["api_status"], result["api_ms"])
        conn.commit()
        print(f"Probe: dns={result['dns_ms']}ms tcp={result['tcp_ms']}ms "
            f"gw_loss={result['ping_gateway_loss']}% net_loss={result['ping_internet_loss']}%")


    print(f"CPU total: {cpu_total:.1f}%")
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
