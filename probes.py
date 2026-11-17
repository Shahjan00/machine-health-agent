import re
import socket
import subprocess
import time
import urllib.request

INTERNET_HOST = "8.8.8.8"
DNS_HOST = "google.com"
TCP_HOST = "google.com"
TCP_PORT = 443
API_URL = "https://www.google.com"

def get_gateway():
    out = subprocess.run(["ip", "route", "show", "default"],
                         capture_output=True, text=True, timeout=5).stdout
    parts = out.split()
    return parts[2] if "via" in parts else None

def check_dns(host=DNS_HOST):
    start = time.time()
    try:
        socket.getaddrinfo(host, 443)
        return (time.time() - start) * 1000
    except OSError:
        return None

def check_tcp(host=TCP_HOST, port=TCP_PORT):
    start = time.time()
    try:
        socket.create_connection((host, port), timeout=5).close()
        return (time.time() - start) * 1000
    except OSError:
        return None

def check_ping(host):
    try:
        out = subprocess.run(["ping", "-c", "3", "-W", "1", host],
                             capture_output=True, text=True, timeout=10).stdout
        match = re.search(r"(\d+(?:\.\d+)?)% packet loss", out)
        return float(match.group(1)) if match else 100.0
    except (OSError, subprocess.TimeoutExpired):
        return 100.0

def check_api(url=API_URL):
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, (time.time() - start) * 1000
    except Exception:
        return 0, None

def run_all():
    gateway = get_gateway()
    dns_ms = check_dns()
    tcp_ms = check_tcp()
    ping_gw = check_ping(gateway) if gateway else 100.0
    ping_net = check_ping(INTERNET_HOST)
    api_status, api_ms = check_api()
    return {
        "dns_ms": dns_ms,
        "tcp_ms": tcp_ms,
        "ping_gateway_loss": ping_gw,
        "ping_internet_loss": ping_net,
        "gateway_ok": 1 if gateway and ping_gw < 100 else 0,
        "api_status": api_status,
        "api_ms": api_ms,
    }
