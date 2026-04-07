import argparse
import logging
import os
import signal
import threading
import time
from typing import Dict, Optional, Tuple

import psutil
from prometheus_client import Counter, Gauge, Histogram, make_wsgi_app
from wsgiref.simple_server import make_server, WSGIRequestHandler

# --- МЕТРИКИ PROMETHEUS ---
# CPU
CPU_USAGE = Gauge('nanopi_cpu_usage_percent', 'CPU usage in percent')
CPU_FREQ = Gauge('nanopi_cpu_frequency_mhz', 'CPU frequency in MHz')
CPU_CORES = Gauge('nanopi_cpu_cores', 'Number of CPU cores')
CPU_USAGE_PER_CORE = Gauge('nanopi_cpu_core_usage_percent', 'CPU usage per core in percent', ['cpu'])
CPU_FREQ_PER_CORE = Gauge('nanopi_cpu_core_frequency_mhz', 'CPU frequency per core in MHz', ['cpu'])

# Memory
MEM_TOTAL = Gauge('nanopi_memory_total_bytes', 'Total memory in bytes')
MEM_AVAILABLE = Gauge('nanopi_memory_available_bytes', 'Available memory in bytes')
MEM_USED = Gauge('nanopi_memory_used_percent', 'Used memory percentage')

# Disk
DISK_TOTAL = Gauge('nanopi_disk_total_bytes', 'Total disk space in bytes', ['mountpoint'])
DISK_USED = Gauge('nanopi_disk_used_bytes', 'Used disk space in bytes', ['mountpoint'])
DISK_FREE = Gauge('nanopi_disk_free_bytes', 'Free disk space in bytes', ['mountpoint'])
DISK_PERCENT = Gauge('nanopi_disk_usage_percent', 'Disk usage percentage', ['mountpoint'])
DISK_INODES_TOTAL = Gauge('nanopi_disk_inodes_total', 'Total inodes', ['mountpoint'])
DISK_INODES_FREE = Gauge('nanopi_disk_inodes_free', 'Free inodes', ['mountpoint'])
DISK_INODES_USED = Gauge('nanopi_disk_inodes_used', 'Used inodes', ['mountpoint'])
DISK_INODES_PERCENT = Gauge('nanopi_disk_inodes_usage_percent', 'Inode usage percentage', ['mountpoint'])
DISK_READ_BYTES = Counter('nanopi_disk_read_bytes_total', 'Disk bytes read', ['device'])
DISK_WRITE_BYTES = Counter('nanopi_disk_write_bytes_total', 'Disk bytes written', ['device'])
DISK_READ_OPS = Counter('nanopi_disk_reads_completed_total', 'Disk read operations completed', ['device'])
DISK_WRITE_OPS = Counter('nanopi_disk_writes_completed_total', 'Disk write operations completed', ['device'])

# Network
NET_BYTES_RECV = Counter('nanopi_network_receive_bytes_total', 'Network bytes received', ['interface'])
NET_BYTES_SENT = Counter('nanopi_network_transmit_bytes_total', 'Network bytes transmitted', ['interface'])
NET_PACKETS_RECV = Counter('nanopi_network_receive_packets_total', 'Network packets received', ['interface'])
NET_PACKETS_SENT = Counter('nanopi_network_transmit_packets_total', 'Network packets transmitted', ['interface'])
NET_ERRIN = Counter('nanopi_network_receive_errors_total', 'Network receive errors', ['interface'])
NET_ERROUT = Counter('nanopi_network_transmit_errors_total', 'Network transmit errors', ['interface'])
NET_DROPIN = Counter('nanopi_network_receive_dropped_total', 'Network receive dropped packets', ['interface'])
NET_DROPOUT = Counter('nanopi_network_transmit_dropped_total', 'Network transmit dropped packets', ['interface'])
NET_IFACE_UP = Gauge('nanopi_network_interface_up', '1 if interface is up, else 0', ['interface'])
NET_IFACE_MTU = Gauge('nanopi_network_interface_mtu', 'Network interface MTU', ['interface'])
NET_IFACE_SPEED_MBIT = Gauge('nanopi_network_interface_speed_mbit', 'Network interface speed (Mbit/s) if available', ['interface'])

# System
TEMPERATURE = Gauge('nanopi_temperature_celsius', 'Hardware temperature in Celsius')
TEMPERATURE_AVAILABLE = Gauge('nanopi_temperature_available', '1 if temperature is available, else 0')
THERMAL_ZONE_TEMPERATURE = Gauge('nanopi_thermal_zone_temperature_celsius', 'Thermal zone temperature in Celsius', ['zone', 'type'])
UPTIME = Gauge('nanopi_uptime_seconds', 'System uptime in seconds')
LOAD1 = Gauge('nanopi_load1', 'System load average over 1 minute')
LOAD5 = Gauge('nanopi_load5', 'System load average over 5 minutes')
LOAD15 = Gauge('nanopi_load15', 'System load average over 15 minutes')

EXPORTER_COLLECT_SECONDS = Histogram(
    'nanopi_exporter_collect_seconds',
    'Time spent collecting and updating metrics',
)
EXPORTER_ERRORS_TOTAL = Counter(
    'nanopi_exporter_errors_total',
    'Number of exporter collection errors',
    ['component'],
)
EXPORTER_READY = Gauge('nanopi_exporter_ready', '1 if exporter is ready, else 0')
EXPORTER_LAST_SUCCESS_TS = Gauge('nanopi_exporter_last_collect_success_timestamp', 'Unix timestamp of last successful collect')


logger = logging.getLogger("nanopi_exporter")
_shutdown = False
_last_net: Dict[str, Tuple[int, int, int, int, int, int, int, int]] = {}
_last_disk: Dict[str, Tuple[int, int, int, int]] = {}
_last_success_ts: float = 0.0


def get_temperature():
    """Чтение температуры CPU, специфичное для Armbian / Nano Pi"""
    # Обычно Armbian хранит температуру здесь:
    paths_to_check = [
        '/sys/class/thermal/thermal_zone0/temp',
        '/sys/devices/virtual/thermal/thermal_zone0/temp'
    ]
    
    for path in paths_to_check:
        try:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    temp_millis = int(f.read().strip())
                    return temp_millis / 1000.0 # Конвертация из миллиградусов в градусы
        except Exception:
            continue
            
    # Запасной вариант через psutil
    try:
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if 'cpu_thermal' in temps:
                return temps['cpu_thermal'][0].current
            for name, entries in temps.items():
                for entry in entries:
                    if entry.current:
                        return float(entry.current)
    except Exception:
        pass
        
    return None


def _parse_csv_list(value: str) -> Tuple[str, ...]:
    items = [v.strip() for v in (value or "").split(",")]
    return tuple(v for v in items if v)

def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return None


def _get_loadavg() -> Optional[Tuple[float, float, float]]:
    try:
        return os.getloadavg()
    except Exception:
        return None


def _collect_thermal_zones() -> Tuple[Optional[float], bool]:
    """
    Returns (best_temp, available). Also populates per-zone metric.
    best_temp is the max temperature found among zones (common for SoC monitoring).
    """
    best: Optional[float] = None
    any_ok = False

    base = "/sys/class/thermal"
    try:
        entries = os.listdir(base)
    except Exception:
        entries = []

    for name in entries:
        if not name.startswith("thermal_zone"):
            continue
        zone = name.replace("thermal_zone", "")
        tpath = os.path.join(base, name, "temp")
        typath = os.path.join(base, name, "type")

        raw = _read_text(tpath)
        if raw is None:
            continue

        try:
            milli = int(float(raw))
            temp_c = milli / 1000.0 if abs(milli) > 1000 else float(raw)
        except Exception:
            continue

        ztype = _read_text(typath) or "unknown"
        THERMAL_ZONE_TEMPERATURE.labels(zone=zone, type=ztype).set(temp_c)
        any_ok = True
        if best is None or temp_c > best:
            best = temp_c

    return best, any_ok


def _disk_inodes(mountpoint: str) -> Optional[Tuple[int, int, int, float]]:
    try:
        st = os.statvfs(mountpoint)
        total = int(st.f_files)
        free = int(st.f_ffree)
        used = total - free if total >= free else 0
        percent = (used / total) * 100.0 if total > 0 else 0.0
        return total, free, used, percent
    except Exception:
        return None


def _iface_sysfs_paths(iface: str) -> Dict[str, str]:
    base = f"/sys/class/net/{iface}"
    return {
        "operstate": os.path.join(base, "operstate"),
        "mtu": os.path.join(base, "mtu"),
        "speed": os.path.join(base, "speed"),
    }


def update_metrics(skip_fstypes: Tuple[str, ...], ignore_ifaces: Tuple[str, ...]):
    """Сбор и обновление метрик"""
    global _last_net, _last_disk, _last_success_ts

    with EXPORTER_COLLECT_SECONDS.time():
        ok = True

        # --- CPU ---
        try:
            CPU_USAGE.set(psutil.cpu_percent(interval=None))
            CPU_CORES.set(psutil.cpu_count(logical=True))

            freq = psutil.cpu_freq()
            if freq:
                CPU_FREQ.set(freq.current)

            # per-core
            per_core = psutil.cpu_percent(interval=None, percpu=True)
            for idx, v in enumerate(per_core):
                CPU_USAGE_PER_CORE.labels(cpu=str(idx)).set(float(v))

            per_core_freq = psutil.cpu_freq(percpu=True)
            if per_core_freq:
                for idx, f in enumerate(per_core_freq):
                    if f:
                        CPU_FREQ_PER_CORE.labels(cpu=str(idx)).set(float(f.current))
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="cpu").inc()
            logger.exception("CPU metrics collection failed")
            ok = False

        # --- Память ---
        try:
            mem = psutil.virtual_memory()
            MEM_TOTAL.set(mem.total)
            MEM_AVAILABLE.set(mem.available)
            MEM_USED.set(mem.percent)
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="memory").inc()
            logger.exception("Memory metrics collection failed")
            ok = False

        # --- Диск ---
        try:
            partitions = psutil.disk_partitions()
            for partition in partitions:
                if partition.fstype in skip_fstypes:
                    continue

                try:
                    disk = psutil.disk_usage(partition.mountpoint)
                    DISK_TOTAL.labels(mountpoint=partition.mountpoint).set(disk.total)
                    DISK_USED.labels(mountpoint=partition.mountpoint).set(disk.used)
                    DISK_FREE.labels(mountpoint=partition.mountpoint).set(disk.free)
                    DISK_PERCENT.labels(mountpoint=partition.mountpoint).set(disk.percent)

                    inode = _disk_inodes(partition.mountpoint)
                    if inode is not None:
                        itotal, ifree, iused, ipct = inode
                        DISK_INODES_TOTAL.labels(mountpoint=partition.mountpoint).set(itotal)
                        DISK_INODES_FREE.labels(mountpoint=partition.mountpoint).set(ifree)
                        DISK_INODES_USED.labels(mountpoint=partition.mountpoint).set(iused)
                        DISK_INODES_PERCENT.labels(mountpoint=partition.mountpoint).set(ipct)
                except PermissionError:
                    continue
                except FileNotFoundError:
                    continue
                except Exception:
                    EXPORTER_ERRORS_TOTAL.labels(component="disk").inc()
                    logger.exception("Disk metrics collection failed for mountpoint=%s", partition.mountpoint)
                    ok = False
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="disk").inc()
            logger.exception("Disk partitions collection failed")
            ok = False

        # --- Disk IO ---
        try:
            io = psutil.disk_io_counters(perdisk=True)
            for dev, stat in io.items():
                cur = (int(stat.read_bytes), int(stat.write_bytes), int(stat.read_count), int(stat.write_count))
                prev = _last_disk.get(dev)
                if prev is not None:
                    d = (cur[0] - prev[0], cur[1] - prev[1], cur[2] - prev[2], cur[3] - prev[3])
                    d = tuple(max(0, x) for x in d)
                    if d[0]:
                        DISK_READ_BYTES.labels(device=dev).inc(d[0])
                    if d[1]:
                        DISK_WRITE_BYTES.labels(device=dev).inc(d[1])
                    if d[2]:
                        DISK_READ_OPS.labels(device=dev).inc(d[2])
                    if d[3]:
                        DISK_WRITE_OPS.labels(device=dev).inc(d[3])
                _last_disk[dev] = cur
            existing = set(io.keys())
            for dev in list(_last_disk.keys()):
                if dev not in existing:
                    _last_disk.pop(dev, None)
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="disk_io").inc()
            logger.exception("Disk IO metrics collection failed")
            ok = False

        # --- Сеть ---
        try:
            net_io = psutil.net_io_counters(pernic=True)
            for interface, io in net_io.items():
                if interface in ignore_ifaces:
                    continue

                cur = (
                    int(io.bytes_recv),
                    int(io.bytes_sent),
                    int(getattr(io, "packets_recv", 0)),
                    int(getattr(io, "packets_sent", 0)),
                    int(getattr(io, "errin", 0)),
                    int(getattr(io, "errout", 0)),
                    int(getattr(io, "dropin", 0)),
                    int(getattr(io, "dropout", 0)),
                )
                prev = _last_net.get(interface)

                if prev is not None:
                    deltas = tuple(max(0, cur[i] - prev[i]) for i in range(len(cur)))
                    if deltas[0]:
                        NET_BYTES_RECV.labels(interface=interface).inc(deltas[0])
                    if deltas[1]:
                        NET_BYTES_SENT.labels(interface=interface).inc(deltas[1])
                    if deltas[2]:
                        NET_PACKETS_RECV.labels(interface=interface).inc(deltas[2])
                    if deltas[3]:
                        NET_PACKETS_SENT.labels(interface=interface).inc(deltas[3])
                    if deltas[4]:
                        NET_ERRIN.labels(interface=interface).inc(deltas[4])
                    if deltas[5]:
                        NET_ERROUT.labels(interface=interface).inc(deltas[5])
                    if deltas[6]:
                        NET_DROPIN.labels(interface=interface).inc(deltas[6])
                    if deltas[7]:
                        NET_DROPOUT.labels(interface=interface).inc(deltas[7])

                _last_net[interface] = cur

                # interface state/mtu/speed via sysfs (best-effort)
                paths = _iface_sysfs_paths(interface)
                oper = _read_text(paths["operstate"])
                NET_IFACE_UP.labels(interface=interface).set(1 if oper == "up" else 0)
                mtu = _read_text(paths["mtu"])
                if mtu and mtu.isdigit():
                    NET_IFACE_MTU.labels(interface=interface).set(int(mtu))
                speed = _read_text(paths["speed"])
                if speed and speed.lstrip("-").isdigit():
                    s = int(speed)
                    if s >= 0:
                        NET_IFACE_SPEED_MBIT.labels(interface=interface).set(s)

            # avoid unbounded growth
            existing = set(net_io.keys())
            for iface in list(_last_net.keys()):
                if iface not in existing:
                    _last_net.pop(iface, None)
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="network").inc()
            logger.exception("Network metrics collection failed")
            ok = False

        # --- Температура ---
        try:
            # Prefer full sysfs thermal zones; fallback to legacy getter.
            best, available = _collect_thermal_zones()
            if not available:
                best = get_temperature()
                available = best is not None

            if not available or best is None:
                TEMPERATURE_AVAILABLE.set(0)
                TEMPERATURE.set(float("nan"))
            else:
                TEMPERATURE_AVAILABLE.set(1)
                TEMPERATURE.set(float(best))
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="temperature").inc()
            TEMPERATURE_AVAILABLE.set(0)
            TEMPERATURE.set(float("nan"))
            logger.exception("Temperature collection failed")
            ok = False

        # --- Аптайм ---
        try:
            uptime = time.time() - psutil.boot_time()
            UPTIME.set(uptime)
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="uptime").inc()
            logger.exception("Uptime collection failed")
            ok = False

        # --- Load average ---
        try:
            la = _get_loadavg()
            if la is not None:
                LOAD1.set(float(la[0]))
                LOAD5.set(float(la[1]))
                LOAD15.set(float(la[2]))
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="loadavg").inc()
            logger.exception("Loadavg collection failed")
            ok = False

        if ok:
            _last_success_ts = time.time()
            EXPORTER_LAST_SUCCESS_TS.set(_last_success_ts)
            EXPORTER_READY.set(1)
        else:
            EXPORTER_READY.set(0)


def _setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _handle_signal(signum, frame):
    global _shutdown
    _shutdown = True


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return int(v)


def _env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None else v


def parse_args():
    parser = argparse.ArgumentParser(description="NanoPi Neo Plus 2 Prometheus exporter")
    parser.add_argument("--port", type=int, default=_env_int("NANOPI_EXPORTER_PORT", 9101))
    parser.add_argument("--bind", type=str, default=_env_str("NANOPI_EXPORTER_BIND", "0.0.0.0"))
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("NANOPI_EXPORTER_INTERVAL_SECONDS", "5")),
    )
    parser.add_argument(
        "--skip-fstypes",
        type=str,
        default=_env_str("NANOPI_EXPORTER_SKIP_FSTYPES", "squashfs,tmpfs,devtmpfs,overlay"),
        help="Comma-separated fstype list to ignore",
    )
    parser.add_argument(
        "--ignore-ifaces",
        type=str,
        default=_env_str("NANOPI_EXPORTER_IGNORE_IFACES", "lo"),
        help="Comma-separated interface list to ignore",
    )
    parser.add_argument("--log-level", type=str, default=_env_str("NANOPI_EXPORTER_LOG_LEVEL", "INFO"))
    parser.add_argument(
        "--ready-max-age-seconds",
        type=float,
        default=float(os.getenv("NANOPI_EXPORTER_READY_MAX_AGE_SECONDS", "30")),
        help="If last successful collect is older than this, /readyz returns 503",
    )
    return parser.parse_args()

class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        # suppress default access logs; rely on app logging instead
        return


def _make_app(ready_max_age_seconds: float):
    prom_app = make_wsgi_app()

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "/")
        if path == "/metrics":
            return prom_app(environ, start_response)
        if path in ("/", ""):
            start_response("302 Found", [("Location", "/metrics")])
            return [b""]
        if path == "/healthz":
            start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"ok\n"]
        if path == "/readyz":
            now = time.time()
            age = now - _last_success_ts if _last_success_ts else 1e9
            if age <= float(ready_max_age_seconds):
                start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
                return [b"ready\n"]
            start_response("503 Service Unavailable", [("Content-Type", "text/plain; charset=utf-8")])
            return [b"not ready\n"]

        start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
        return [b"not found\n"]

    return app


if __name__ == '__main__':
    args = parse_args()
    _setup_logging(args.log_level)

    skip_fstypes = _parse_csv_list(args.skip_fstypes)
    ignore_ifaces = _parse_csv_list(args.ignore_ifaces)

    logger.info("Starting NanoPi Exporter on %s:%s", args.bind, args.port)
    app = _make_app(args.ready_max_age_seconds)
    httpd = make_server(args.bind, int(args.port), app, handler_class=_QuietHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, name="httpd", daemon=True)
    server_thread.start()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Первый вызов для инициализации cpu_percent
    psutil.cpu_percent()

    # Бесконечный цикл обновления метрик
    while not _shutdown:
        try:
            update_metrics(skip_fstypes=skip_fstypes, ignore_ifaces=ignore_ifaces)
        except Exception:
            EXPORTER_ERRORS_TOTAL.labels(component="loop").inc()
            logger.exception("Unhandled error in update loop")

        time.sleep(max(0.1, float(args.interval_seconds)))

    try:
        httpd.shutdown()
        httpd.server_close()
    except Exception:
        pass
    logger.info("Shutdown requested, exiting")
