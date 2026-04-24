#!/usr/bin/env python3
"""
mizonam — Asymmetric Upload Balancer for Iranian servers
Version  : 1.0.4-defaults
Requires : Python 3.6+  (pre-installed on Ubuntu 18.04+)
Deps     : ZERO  (stdlib only)
"""

import sys, os, json, socket, struct, random, threading
import time, signal, subprocess, shutil, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ══════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════
VERSION      = "1.0.4-defaults"
CONFIG_DIR   = Path("/etc/mizonam")
CONFIG_FILE  = CONFIG_DIR / "config.json"
STATE_FILE   = CONFIG_DIR / "state.json"
LOG_FILE     = Path("/var/log/mizonam.log")
INSTALL_PATH = Path("/usr/local/bin/mizonam")
SERVICE_NAME = "mizonam"

# Iranian IP CIDR ranges (public RIPE NCC data)
IRANIAN_CIDRS = [
    "2.144.0.0/13",  "5.22.0.0/17",   "5.52.0.0/15",   "5.200.0.0/16",
    "31.2.128.0/17", "31.40.0.0/14",  "37.0.8.0/21",   "37.98.0.0/16",
    "37.156.0.0/14", "46.100.0.0/14", "46.209.0.0/16", "62.193.0.0/16",
    "62.220.0.0/15", "78.38.0.0/15",  "78.157.0.0/17", "80.191.0.0/17",
    "80.210.0.0/15", "82.99.192.0/18","85.9.64.0/18",  "85.133.128.0/17",
    "87.247.136.0/21","87.248.0.0/14","89.32.0.0/11",  "89.144.0.0/13",
    "91.186.192.0/18","93.110.0.0/15","94.182.0.0/15", "94.184.0.0/14",
    "95.38.0.0/15",  "95.64.0.0/14",  "109.72.64.0/18","176.65.192.0/18",
    "185.8.172.0/22","185.55.224.0/22","185.81.96.0/22","185.116.160.0/22",
    "185.155.36.0/22","185.200.112.0/22",
]

# Per-hour thread multiplier (Tehran time, index = hour 0-23)
HOUR_MUL = [
    2.0, 1.6, 1.0, 0.6, 0.2, 0.1,
    0.6, 1.0, 1.2, 1.3, 1.4, 1.5,
    1.3, 1.4, 1.6, 1.5, 1.3, 1.5,
    1.7, 1.8, 2.0, 1.3, 1.5, 1.8,
]

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))

DEFAULT_CONFIG = {
    "enabled"      : True,
    "interface"    : "auto",
    "coefficient"  : 3,
    "threads"      : 5,
    "buffer_kb"    : 16,         # ← Your requested default
    "custom_cidrs" : [],
    "debug"        : True,       # ← Your requested default (ON)
    "custom_only"  : False,
}

SYSTEMD_UNIT = """\
[Unit]
Description=Mizonam – Asymmetric Upload Balancer
After=network.target

[Service]
ExecStart={bin} daemon
Restart=always
RestartSec=15
User=root

[Install]
WantedBy=multi-user.target
"""

# ══════════════════════════════════════════════════════════════
#  ANSI HELPERS
# ══════════════════════════════════════════════════════════════
R  = "\033[0m"
B  = "\033[1m"
D  = "\033[2m"
RE = "\033[91m"; GR = "\033[92m"; YE = "\033[93m"
BL = "\033[94m"; MA = "\033[95m"; CY = "\033[96m"; WH = "\033[97m"
CLEAR = "\033[2J\033[H"

def c(text, *attrs): return "".join(attrs) + str(text) + R
def box(s): return c(s, CY)


# ══════════════════════════════════════════════════════════════
#  CONFIG / STATE / NETWORK MONITOR / IP GENERATOR
# ══════════════════════════════════════════════════════════════
class Config:
    def __init__(self):
        self._d = dict(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    self._d.update(json.load(f))
            except Exception:
                pass
        return self

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(self._d, f, indent=2)

    def get(self, k, default=None):
        return self._d.get(k, default if default is not None else DEFAULT_CONFIG.get(k))

    def set(self, k, v):
        self._d[k] = v
        self.save()


class State:
    def __init__(self):
        self._d = {}
        self.load()

    def load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE) as f:
                    self._d = json.load(f)
            except Exception:
                self._d = {}
        return self

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(self._d, f)

    def get(self, k, default=0): return self._d.get(k, default)

    def set(self, k, v):
        self._d[k] = v
        self.save()


class NetworkMonitor:
    def __init__(self, interface="auto"):
        self.interface = self._detect(interface)
        self._state    = State()

    def _detect(self, iface):
        if iface and iface != "auto":
            return iface
        try:
            with open("/proc/net/route") as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) > 1 and parts[1] == "00000000":
                        return parts[0]
        except Exception:
            pass
        try:
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:
                    name = line.split(":")[0].strip()
                    if name != "lo":
                        return name
        except Exception:
            pass
        return "eth0"

    def _raw(self):
        try:
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:
                    if self.interface + ":" in line:
                        parts = line.split()
                        return int(parts[9]), int(parts[1])
        except Exception:
            pass
        return 0, 0

    def get_counters(self):
        raw_up, raw_down = self._raw()
        cached_up   = self._state.get("cached_up",   raw_up)
        cached_down = self._state.get("cached_down", raw_down)
        sync_up     = self._state.get("sync_up",   0)
        sync_down   = self._state.get("sync_down", 0)

        if raw_up < cached_up or raw_down < cached_down:
            sync_up   += cached_up
            sync_down += cached_down
            self._state.set("sync_up",   sync_up)
            self._state.set("sync_down", sync_down)

        self._state.set("cached_up",   raw_up)
        self._state.set("cached_down", raw_down)
        return raw_up + sync_up, raw_down + sync_down


class IPGenerator:
    def __init__(self, cidrs):
        self._pools = []
        for cidr in cidrs:
            try:
                net, prefix = cidr.strip().split("/")
                prefix = int(prefix)
                base   = struct.unpack("!I", socket.inet_aton(net))[0]
                mask   = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
                base  &= mask
                count  = max(0, (1 << (32 - prefix)) - 2)
                if count > 0:
                    self._pools.append((base, count))
            except Exception:
                pass

    def random_ip(self):
        if not self._pools:
            return "10.0.0.1"
        base, count = random.choice(self._pools)
        return socket.inet_ntoa(struct.pack("!I", base + random.randint(1, count)))

    @staticmethod
    def random_port(): return random.randint(1024, 65535)


# ══════════════════════════════════════════════════════════════
#  UDP UPLOADER (MTU auto-adjust still active)
# ══════════════════════════════════════════════════════════════
class UDPUploader:
    def __init__(self, ip_gen, buffer_kb=16, debug=True):
        self._ip_gen       = ip_gen
        self._base_kb      = buffer_kb
        self._payload_size = buffer_kb * 1024
        self._payload      = os.urandom(self._payload_size)
        self._sent         = 0
        self._lock         = threading.Lock()

        self._max_payload  = self._payload_size
        self._mtu_lock     = threading.Lock()
        self._mtu_errors   = 0

        self._debug        = debug
        self._sample_lock  = threading.Lock()
        self._sample_ips   = []
        self._error_count  = 0

    @property
    def sent(self):
        with self._lock: return self._sent

    def reset(self):
        with self._lock: self._sent = 0
        with self._sample_lock:
            self._sample_ips.clear()
            self._error_count = 0
        with self._mtu_lock:
            self._mtu_errors = 0

    def _worker(self, quota, stop):
        sent = 0
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.3)

            while sent < quota and not stop.is_set():
                dst_ip   = self._ip_gen.random_ip()
                dst_port = self._ip_gen.random_port()

                with self._mtu_lock:
                    current_size = self._max_payload
                payload = os.urandom(current_size) if current_size != self._payload_size else self._payload

                try:
                    sock.sendto(payload, (dst_ip, dst_port))
                    n = len(payload)
                    sent += n
                    with self._lock: self._sent += n

                    if self._debug and random.random() < 0.005:
                        with self._sample_lock:
                            if len(self._sample_ips) < 20:
                                self._sample_ips.append(f"{dst_ip}:{dst_port}")

                except OSError as e:
                    if e.errno == 90:  # Message too long
                        with self._mtu_lock:
                            self._mtu_errors += 1
                            if self._mtu_errors >= 5 and self._max_payload > 512:
                                self._max_payload = max(512, self._max_payload // 2)
                                log(f"MTU too big → auto-reduced buffer to {self._max_payload//1024} KB", "WARN")
                        if self._debug and random.random() < 0.1:
                            log(f"UDP MTU error to {dst_ip}:{dst_port} → reduced to {self._max_payload//1024}KB", "WARN")
                    else:
                        with self._sample_lock:
                            self._error_count += 1
                        if self._debug:
                            log(f"UDP send error to {dst_ip}:{dst_port} → {type(e).__name__}: {str(e)[:100]}", "WARN")
                except Exception as e:
                    with self._sample_lock:
                        self._error_count += 1
                    if self._debug:
                        log(f"UDP send error to {dst_ip}:{dst_port} → {type(e).__name__}: {str(e)[:100]}", "WARN")

        finally:
            try: sock.close()
            except Exception: pass

    def upload(self, total_bytes, n_threads):
        if total_bytes <= 0 or n_threads <= 0:
            return 0
        per = max(1, total_bytes // n_threads)
        stop = threading.Event()
        threads = [threading.Thread(target=self._worker, args=(per, stop), daemon=True) for _ in range(n_threads)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=90)
        return self.sent


# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════
def tehran_hour():
    return datetime.now(TEHRAN_TZ).hour

def scaled_threads(base):
    mul = HOUR_MUL[tehran_hour()]
    lo  = max(1, int(base * mul * 0.8))
    hi  = max(1, int(base * mul * 1.2))
    return random.randint(lo, hi)

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{ts}] [{level}] {msg}\n")
    except Exception:
        pass

def fmt_bytes(n):
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def run(cmd):
    r = subprocess.run(cmd if isinstance(cmd, list) else cmd.split(), capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()

def svc_status():
    _, out = run("systemctl is-active mizonam")
    return "running" if out.strip() == "active" else "stopped"


# ══════════════════════════════════════════════════════════════
#  DAEMON LOOP
# ══════════════════════════════════════════════════════════════
def run_daemon():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    cfg = Config()
    monitor = NetworkMonitor(cfg.get("interface"))

    custom_cidrs = cfg.get("custom_cidrs") or []
    custom_only  = cfg.get("custom_only", False)
    all_cidrs    = custom_cidrs if (custom_only and custom_cidrs) else IRANIAN_CIDRS + custom_cidrs
    ip_gen       = IPGenerator(all_cidrs)
    uploader     = UDPUploader(ip_gen, cfg.get("buffer_kb"), debug=cfg.get("debug", True))

    log(f"Mizonam v{VERSION} daemon started | iface={monitor.interface} | custom_only={custom_only} | debug=ON | buffer={cfg.get('buffer_kb')}KB")

    while True:
        cfg.load()

        if not cfg.get("enabled"):
            time.sleep(30)
            continue

        custom_cidrs = cfg.get("custom_cidrs") or []
        custom_only  = cfg.get("custom_only", False)
        if custom_only and custom_cidrs:
            all_cidrs = custom_cidrs
        elif custom_only and not custom_cidrs:
            all_cidrs = IRANIAN_CIDRS
            log("Custom-Only mode enabled but no custom CIDRs → fallback", "WARN")
        else:
            all_cidrs = IRANIAN_CIDRS + custom_cidrs

        ip_gen    = IPGenerator(all_cidrs)
        uploader  = UDPUploader(ip_gen, cfg.get("buffer_kb"), debug=cfg.get("debug", True))

        upload, download = monitor.get_counters()
        coeff = float(cfg.get("coefficient")) * random.uniform(0.7, 1.3)
        gap   = download * coeff - upload

        if gap < 1_000_000_000:
            time.sleep(random.randint(30, 90))
            continue

        n_threads = scaled_threads(cfg.get("threads"))
        log(f"Cycle | gap={fmt_bytes(gap)} threads={n_threads} up={fmt_bytes(upload)} dl={fmt_bytes(download)}")

        uploader.reset()
        remaining = gap
        budget    = gap

        while remaining > 0.1 * budget:
            batch = min(0.3 * budget, remaining)
            sent  = uploader.upload(int(batch), n_threads)
            remaining -= max(sent, 1)
            if sent == 0: break
            time.sleep(random.randint(5, 30))

        log(f"Cycle done | sent={fmt_bytes(uploader.sent)}")

        if getattr(uploader, '_debug', True):
            with uploader._sample_lock:
                samples = list(uploader._sample_ips)[:8]
                errors  = uploader._error_count
            if samples or errors > 0:
                sample_str = ", ".join(samples) + (" ..." if len(samples) == 8 else "")
                log(f"DEBUG → Samples: {sample_str or 'none'} | Errors: {errors}", "WARN" if errors else "INFO")
            with uploader._sample_lock:
                uploader._sample_ips.clear()
                uploader._error_count = 0

        time.sleep(random.randint(10, 30))


# ══════════════════════════════════════════════════════════════
#  INSTALLER + MENU
# ══════════════════════════════════════════════════════════════
def do_install():
    if os.geteuid() != 0:
        print(c("✗ Run with sudo.", RE)); sys.exit(1)

    src = Path(sys.argv[0]).resolve()
    if not src.exists() or str(src) == "-":
        print(c("✗ Cannot detect script path.", RE)); sys.exit(1)

    INSTALL_PATH.parent.mkdir(parents=True, exist_ok=True)
    copy_needed = True
    try:
        if os.path.samefile(str(src), str(INSTALL_PATH)):
            copy_needed = False
    except OSError:
        copy_needed = True

    if copy_needed:
        shutil.copy2(src, INSTALL_PATH)
        print(c(f"✓ Installed/updated to {INSTALL_PATH}", GR))
    else:
        print(c(f"✓ Already installed (up-to-date)", GR))

    os.chmod(INSTALL_PATH, 0o755)

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        Config().save()
        print(c(f"✓ Config created with new defaults (buffer=16KB, debug=ON)", GR))
    else:
        print(c(f"✓ Config already exists", GR))

    svc_path = Path("/etc/systemd/system/mizonam.service")
    svc_path.write_text(SYSTEMD_UNIT.format(bin=INSTALL_PATH))
    run("systemctl daemon-reload")
    run("systemctl enable mizonam")
    # FIX 1: از restart به جای start استفاده می‌کنیم
    # تا در صورتی که سرویس قبلاً در حال اجرا بوده، مطمئن بشیم با کد جدید ری‌استارت می‌شه
    ok, _ = run("systemctl restart mizonam")
    print(c("✓ Service enabled & started", GR) if ok else c("⚠ Service start failed", YE))

    print()
    print(c(f"  Run:  mizonam menu", CY + B))


def _bar(ratio, target, width=20):
    filled = min(width, int(ratio / target * width)) if target > 0 else 0
    color  = GR if ratio >= target else (YE if ratio >= target * 0.5 else RE)
    return color + "█" * filled + D + "░" * (width - filled) + R


def menu():
    cfg     = Config()
    monitor = NetworkMonitor(cfg.get("interface"))

    while True:
        cfg.load()
        print(CLEAR, end="")

        up, dl       = monitor.get_counters()
        ratio        = up / dl if dl > 0 else 0
        target_r     = float(cfg.get("coefficient"))
        gap          = max(0, dl * target_r - up)
        status       = svc_status()
        iface        = monitor.interface
        hour_mul     = HOUR_MUL[tehran_hour()]
        eff_thr      = int(cfg.get("threads") * hour_mul)
        debug_on     = cfg.get("debug", True)
        custom_only  = cfg.get("custom_only", False)
        custom_count = len(cfg.get("custom_cidrs") or [])
        buffer_kb    = cfg.get("buffer_kb")

        mode_str = "CUSTOM ONLY" if custom_only and custom_count > 0 else \
                   "CUSTOM ONLY (fallback)" if custom_only else "IRANIAN + CUSTOM"
        mode_col = CY if custom_only and custom_count > 0 else YE

        print(box("╔════════════════════════════════════════════════╗"))
        print(box("║") + c(f"   Mizonam  v{VERSION}  —  Upload Balancer     ", B + WH) + box("║"))
        print(box("╚════════════════════════════════════════════════╝"))
        print()

        print(c("  ┌─ Live Stats ──────────────────────────────┐", D))
        print(f"  │  Service   : {c('● RUNNING', GR+B) if status=='running' else c('● STOPPED', RE+B):<30}  │")
        print(f"  │  Interface : {c(iface, YE):<38}│")
        print(f"  │  Download  : {c(fmt_bytes(dl), BL):<38}│")
        print(f"  │  Upload    : {c(fmt_bytes(up), GR):<38}│")
        bar_str = _bar(ratio, target_r)
        print(f"  │  Ratio     : {bar_str}  {c(f'{ratio:.2f}x',MA)} / {target_r}x  │")
        print(f"  │  Gap       : {c(fmt_bytes(gap), RE if gap>1e9 else GR):<38}│")
        print(f"  │  Eff.Thrs  : {c(eff_thr, CY)} (Tehran hour {tehran_hour():02d}:xx) {'':<12}│")
        print(f"  │  Debug mode: {c('ENABLED' if debug_on else 'DISABLED', GR+B if debug_on else D):<38}│")
        print(f"  │  IP Mode   : {c(mode_str, mode_col):<38}│")
        print(f"  │  Buffer    : {c(f'{buffer_kb} KB (auto-adjusted)', YE):<38}│")
        print(c("  └───────────────────────────────────────────┘", D))
        print()

        enabled_str = c("ON ", GR+B) if cfg.get("enabled") else c("OFF", RE+B)
        print(f"  {c('[1]',CY+B)} Toggle uploader ........... [{enabled_str}]")
        print(f"  {c('[2]',CY+B)} Coefficient ................ [{c(cfg.get('coefficient'), YE)}x]")
        print(f"  {c('[3]',CY+B)} Base threads .............. [{c(cfg.get('threads'), YE)}]")
        print(f"  {c('[4]',CY+B)} Buffer size ............... [{c(str(buffer_kb)+' KB', YE)}]")
        print(f"  {c('[5]',CY+B)} Network interface ......... [{c(cfg.get('interface'), YE)}]")
        print(f"  {c('[6]',CY+B)} Add custom CIDR ........... [{c(custom_count, YE)} added]")
        print(f"  {c('[7]',CY+B)} Toggle debug mode ......... [{c('ON ' if debug_on else 'OFF', GR+B if debug_on else RE+B)}]")
        print(f"  {c('[8]',CY+B)} Toggle Custom CIDR-Only ... [{c('ON ' if custom_only else 'OFF', GR+B if custom_only else RE+B)}]")
        print()
        print(f"  {c('[r]',CY+B)} Restart service")
        print(f"  {c('[l]',CY+B)} View last 30 log lines")
        print(f"  {c('[f]',CY+B)} Refresh")
        print(f"  {c('[q]',CY+B)} Quit")
        print()

        try:
            choice = input(c("  ❯ ", CY + B)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print(); break

        if choice == "1":
            cfg.set("enabled", not cfg.get("enabled"))
            run("systemctl restart mizonam")
        elif choice == "2":
            v = input(c("  Coefficient (e.g. 3): ", D)).strip()
            try:
                cfg.set("coefficient", max(0.5, min(20.0, float(v))))
                # FIX 2: بعد از هر تغییر config، سرویس رو ری‌استارت می‌کنیم
                run("systemctl restart mizonam")
            except: pass
        elif choice == "3":
            v = input(c("  Base threads [1-100]: ", D)).strip()
            try:
                cfg.set("threads", max(1, min(100, int(v))))
                # FIX 2
                run("systemctl restart mizonam")
            except: pass
        elif choice == "4":
            v = input(c("  Buffer KB [1-64]: ", D)).strip()
            try:
                cfg.set("buffer_kb", max(1, min(64, int(v))))
                # FIX 2
                run("systemctl restart mizonam")
            except: pass
        elif choice == "5":
            v = input(c("  Interface: ", D)).strip()
            if v:
                cfg.set("interface", v)
                monitor.interface = monitor._detect(v)
                # FIX 2
                run("systemctl restart mizonam")
        elif choice == "6":
            v = input(c("  CIDR to add or blank to list/remove: ", D)).strip()
            if v:
                cidrs = cfg.get("custom_cidrs") or []
                if v not in cidrs:
                    cidrs.append(v)
                    cfg.set("custom_cidrs", cidrs)
                    # FIX 2
                    run("systemctl restart mizonam")
                    print(c(f"  ✓ Added {v}", GR))
                else:
                    print(c("  Already in list.", YE))
            else:
                cidrs = cfg.get("custom_cidrs") or []
                if not cidrs:
                    print(c("  No custom CIDRs.", D))
                else:
                    for i, cidr in enumerate(cidrs):
                        print(f"  {i}: {cidr}")
                    rm = input(c("  Index to remove: ", D)).strip()
                    try:
                        cidrs.pop(int(rm))
                        cfg.set("custom_cidrs", cidrs)
                        # FIX 2
                        run("systemctl restart mizonam")
                        print(c("  ✓ Removed.", GR))
                    except: pass
            time.sleep(1)
        elif choice == "7":
            debug_now = cfg.get("debug", True)
            cfg.set("debug", not debug_now)
            # FIX 2
            run("systemctl restart mizonam")
            print(c(f"  ✓ Debug mode {'ENABLED' if not debug_now else 'DISABLED'}", GR if not debug_now else YE))
            time.sleep(1)
        elif choice == "8":
            now = cfg.get("custom_only", False)
            cfg.set("custom_only", not now)
            # FIX 2
            run("systemctl restart mizonam")
            print(c(f"  ✓ Custom CIDR-Only mode {'ENABLED' if not now else 'DISABLED'}", GR if not now else YE))
            time.sleep(1)
        elif choice == "r":
            run("systemctl restart mizonam")
            print(c("  ✓ Restarted.", GR)); time.sleep(1)
        elif choice == "l":
            print()
            try:
                with open(LOG_FILE) as f:
                    lines = f.readlines()
                for line in lines[-30:]:
                    col = RE if "ERROR" in line else (YE if "WARN" in line else D)
                    print(c("  " + line.rstrip(), col))
            except FileNotFoundError:
                print(c("  No log file yet.", D))
            input(c("\n  Press Enter...", D))
        elif choice in ("f", ""):
            pass
        elif choice == "q":
            print(c("\n  Goodbye!\n", CY)); break


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════
COMMANDS = ["daemon", "menu", "install", "start", "stop", "status", "restart"]

def main():
    p = argparse.ArgumentParser(prog="mizonam")
    p.add_argument("command", nargs="?", default="menu", choices=COMMANDS)
    args = p.parse_args()

    if args.command == "daemon":
        run_daemon()
    elif args.command == "menu":
        menu()
    elif args.command == "install":
        do_install()
    elif args.command == "status":
        cfg = Config()
        mon = NetworkMonitor(cfg.get("interface"))
        up, dl = mon.get_counters()
        print(f"Service  : {svc_status()}")
        print(f"Upload   : {fmt_bytes(up)}")
        print(f"Download : {fmt_bytes(dl)}")
        print(f"Ratio    : {up/dl:.2f}x" if dl else "Ratio: N/A")
    elif args.command in ("start", "stop", "restart"):
        ok, out = run(f"systemctl {args.command} mizonam")
        print(out or ("OK" if ok else "Failed"))
    else:
        menu()

if __name__ == "__main__":
    main()
