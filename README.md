# Machine Health Agent

A single Python process that samples a Linux machine every 5 s, probes the network every 15 s, keeps 7 days of history in SQLite, raises deterministic alerts, and serves everything over an HTTP API and a one-page dashboard.

Built for Zero Forg's edge boxes: when a box fills its disk, leaks memory or loses its uplink, this agent notices first.

## Quick start

```bash
pip install -r requirements.txt     # psutil only
python3 collector.py                # starts sampler + probes + API
# open http://localhost:8080
```

Requirements: Linux, Python 3.8+, and the `ip` and `ping` commands (`iproute2`, `iputils-ping`). No root needed. Everything else is standard library.

## Layout

| File | Role |
|---|---|
| `collector.py` | Entry point. 5 s sampling loop, starts the API and probe threads |
| `probes.py` | DNS, TCP, gateway/internet ping, API probe, all with timeouts |
| `alerts.py` | Alert rules and open/close state machine |
| `db.py` | SQLite schema, inserts, 7-day pruning |
| `api.py` | HTTP API and dashboard server (`http.server`) |
| `config.py` | Thresholds and the process watch list |
| `dashboard.html` | Plain HTML + canvas charts, polls the API every 5 s |

## Architecture

```
main thread   : sample every 5 s -> SQLite -> alert rules
probe thread  : probes.run_all() every ~15 s -> shared result -> main thread stores it and runs network rules
API thread    : ThreadingHTTPServer on :8080, read-only, own SQLite connection per request
SQLite        : health.db, WAL mode
```

Probes run in their own thread, so a hung DNS lookup, a dead gateway or a slow endpoint can never delay the 5 s sampler. The main loop is wrapped in `try/except`, so one bad sample doesn't kill the agent.

## What is collected

Every 5 s:
- CPU total (mean of per-core), RAM %, disk usage **per mounted filesystem** (tmpfs and loopback mounts included, pseudo-filesystems skipped), network byte totals, uptime, load average.
- Top 10 processes by CPU, plus any watch-list processes: PID, name, CPU %, RSS.
Every ~15 s (probes, each with a timeout):
- DNS resolution time (5 s cap), TCP connect time to the resolved IP (5 s cap, so DNS time is not mixed in).
- Packet loss to the default gateway and to an internet host (`ping -c 3 -W 1`, 10 s cap).
- Local network reachable = gateway answers.
- Response time and HTTP status of an API endpoint (5 s cap; 4xx/5xx statuses are recorded, not treated as a probe failure).
A failed DNS/TCP/API probe stores `NULL` for its latency, which shows up as a gap in the data.

## Alert rules

All rules are deterministic. An alert **opens once**, stays open while the condition holds (peak value updated with `MAX`), and **closes** when it clears. Open state lives in the database, so there is no new alert per sample.

| Rule | Opens when | Closes when | Subject |
|---|---|---|---|
| `cpu_high` | Total CPU > 85% continuously for **60 s** (timestamp of the first high sample, reset by any sample ≤ 85%, so it can't fire at second 5) | First sample ≤ 85% | host |
| `ram_high` | RAM > 90% | RAM ≤ 90% | host |
| `disk_high:<mount>` | Any mounted filesystem > 90% | That mount ≤ 90% | mount point |
| `network_lost` | Gateway probe fails **or** internet ping loss is 100%, **3 probe rounds in a row** | First good round | host |
| `memory_climbing:<pid>` | See below | Condition clears or PID exits | PID |
| `process_died:<name>:<pid>` | A watch-list process that was seen alive for ≥ 5 s disappears | A process with that name is running again | name + PID |
| `zombie:<pid>` | A process stays in state `Z` for ≥ 30 s | It leaves state `Z` | PID |
| `proc_cpu_high:<pid>` | One process above `PROC_CPU_THRESHOLD` for 3 consecutive samples | Drops below threshold or exits | PID |
| `proc_ram_high:<pid>` | One process RSS above 1024 MB | Drops below or exits | PID |

### Memory climbing: definition and why

Per process, one RSS sample per minute (every 12th 5 s tick), keeping the last 10. The alert fires when:

- at least **9 of the 9 minute-to-minute steps are rises**, and
- total growth across the window is **≥ 50 MB**.
Why these numbers:
- **9 rises:** with 10 one-minute samples there are 9 steps between them, and all 9 must rise. That is strict on purpose, it keeps false positives low. A process that grows steadily for 10 minutes is a real leak.
- **50 MB floor:** filters processes that grow a few MB from caches, log buffers or warm-up, which would otherwise trip a pure "keeps rising" rule.
- **10 minutes of history:** long enough to separate a leak from startup growth, short enough to catch one within a useful time on a box nobody is watching.
- **Per PID:** the alert names the process that is leaking, not just "memory is high".
### Process died

The watch list is `WATCHED` in `config.py` (default `["sleep"]`, used for the demo). A process only counts as "tracked" after it has been alive for 5 s, so short-lived start-up churn doesn't alert.

## Health status

`GET /health` returns `critical` if any open alert is `cpu_high`, `ram_high`, `disk_high:*` or `network_lost`; `warn` if any other alert is open; otherwise `ok`.

## API

| Endpoint | Returns |
|---|---|
| `GET /health` | `{status, open_alerts}` |
| `GET /metrics` | Latest sample, network rates, latest probe result |
| `GET /metrics?name=<n>&since=<unix_ts>` | Time series. `name` is a system column (`cpu_total`, `ram_percent`, `disk_percent`, `net_sent`, `net_recv`, `uptime`, `load1/5/15`) or a probe column (`dns_ms`, `tcp_ms`, `ping_gateway_loss`, `ping_internet_loss`, `gateway_ok`, `api_status`, `api_ms`) |
| `GET /history?hours=1\|24` | CPU, RAM, disk series for the dashboard charts |
| `GET /alerts?open=true\|false` | Alerts with `opened_ts`, `closed_ts` (unix seconds), `peak`, `status` |
| `GET /processes?sort=cpu\|rss` | Top processes from the latest sample |
| `GET /` | Dashboard |

## Storage and retention

SQLite in WAL mode (so API reads don't block the writer), with an index on `ts` for every table. Every tick prunes rows older than 7 days from all tables, and closed alerts older than 7 days. History is bounded and does not grow forever. Only the top 10 processes are stored per sample.

## Resource budget

Target: under 5% of one core and 150 MB RAM. On a lightly loaded test machine the agent measured about **1% of one core and 28 MB RSS**. Measure on the real box with:

```bash
pidstat -p $(pgrep -f collector.py) 5
```

## Demo scripts

Run `python3 collector.py` first. Watch `curl localhost:8080/alerts` or the dashboard.

```bash
# 1. CPU high: opens ~60 s after load starts, not before
stress --cpu 4 --timeout 120

# 2a. RAM high
stress --vm 1 --vm-bytes 1500M --timeout 120

# 2b. Memory climbing: ~12 MB/min leak. Needs 10 one-minute samples, so start it ~10 min before the alert is due
python3 -c "
import time; l=[]
while True:
    l.append(bytearray(2*1024*1024)); time.sleep(10)"

# 3. Disk high: use a small tmpfs, not the real disk
sudo mkdir -p /mnt/testdisk && sudo mount -t tmpfs -o size=100M tmpfs /mnt/testdisk
fallocate -l 95M /mnt/testdisk/big.bin     # opens disk_high:/mnt/testdisk
rm /mnt/testdisk/big.bin                   # closes it, then: sudo umount /mnt/testdisk

# 4. Process died (keep "sleep" in WATCHED; let it run >5 s first)
sleep 1000 & sleep 10; kill -9 $!

# 5. Network lost, then back (3 failed probe rounds needed, roughly 45-90 s)
sudo ip link set <iface> down
sudo ip link set <iface> up
curl 'localhost:8080/metrics?name=dns_ms&since=0'    # gap while down

# Bonus: zombie (opens after 30 s)
python3 -c "import os,time
if os.fork()==0: os._exit(0)
time.sleep(120)"
```

## Known limitations

Being upfront about what this version does not do yet:

- **Not collected:** swap, disk I/O, packet drops/errors, TCP connection count, listening ports, open file descriptors. Per-core CPU is computed but only the total is stored.
- **Bandwidth** is derived in the API from cumulative byte totals (no per-interface data) and does not handle counter resets yet.
- **No hysteresis:** RAM, disk and CPU alerts close the first sample back under threshold, so a value oscillating exactly at the threshold can flap.
- **Alert schema:** rule and subject are encoded in a single `name` column (`rule:subject`); there is no separate `host` column.
- **Restart behaviour:** in-memory state resets. Open alerts persist, but after a restart `network_lost` can close early while the network is still down, and `zombie`/`memory_climbing` alerts for processes that vanished meanwhile are not reconciled. A `disk_high` alert stays open if its mount disappears.
- **`proc_cpu_high`** uses a threshold of 100 on psutil's per-core CPU %, so a single-threaded hog (max ~100%) rarely triggers it.
- **Process died** closes when any process with that name is running, so with several same-name processes the alert can close quickly.
- **Memory-climbing detection** needs at least 10 minutes by definition; the sample interval is fixed at 60 s.
- **Dashboard** charts CPU only and lists alerts by name and peak; exact open/close timestamps come from `/alerts`.
- **Probe cadence** is "15 s after the previous round finishes", so it stretches when probes are slow. Probe hosts are constants in `probes.py`.
