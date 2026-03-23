import time
import psutil
import os
from prometheus_client import start_http_server, Gauge

# --- МЕТРИКИ PROMETHEUS ---
# CPU
CPU_USAGE = Gauge('nanopi_cpu_usage_percent', 'CPU usage in percent')
CPU_FREQ = Gauge('nanopi_cpu_frequency_mhz', 'CPU frequency in MHz')
CPU_CORES = Gauge('nanopi_cpu_cores', 'Number of CPU cores')

# Memory
MEM_TOTAL = Gauge('nanopi_memory_total_bytes', 'Total memory in bytes')
MEM_AVAILABLE = Gauge('nanopi_memory_available_bytes', 'Available memory in bytes')
MEM_USED = Gauge('nanopi_memory_used_percent', 'Used memory percentage')

# Disk
DISK_TOTAL = Gauge('nanopi_disk_total_bytes', 'Total disk space in bytes', ['mountpoint'])
DISK_USED = Gauge('nanopi_disk_used_bytes', 'Used disk space in bytes', ['mountpoint'])
DISK_FREE = Gauge('nanopi_disk_free_bytes', 'Free disk space in bytes', ['mountpoint'])
DISK_PERCENT = Gauge('nanopi_disk_usage_percent', 'Disk usage percentage', ['mountpoint'])

# Network
NET_BYTES_RECV = Gauge('nanopi_network_receive_bytes_total', 'Network bytes received', ['interface'])
NET_BYTES_SENT = Gauge('nanopi_network_transmit_bytes_total', 'Network bytes transmitted', ['interface'])

# System
TEMPERATURE = Gauge('nanopi_temperature_celsius', 'Hardware temperature in Celsius')
UPTIME = Gauge('nanopi_uptime_seconds', 'System uptime in seconds')


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
        
    return 0.0

def update_metrics():
    """Сбор и обновление метрик"""
    # --- CPU ---
    CPU_USAGE.set(psutil.cpu_percent(interval=None))
    CPU_CORES.set(psutil.cpu_count(logical=True))
    
    freq = psutil.cpu_freq()
    if freq:
        CPU_FREQ.set(freq.current)
        
    # --- Память ---
    mem = psutil.virtual_memory()
    MEM_TOTAL.set(mem.total)
    MEM_AVAILABLE.set(mem.available)
    MEM_USED.set(mem.percent)
    
    # --- Диск ---
    # Мониторим корневой раздел и /boot
    partitions = psutil.disk_partitions()
    for partition in partitions:
        # Пропускаем виртуальные и snap ФС
        if partition.fstype in ('squashfs', 'tmpfs', 'devtmpfs', 'overlay'):
            continue
            
        try:
            disk = psutil.disk_usage(partition.mountpoint)
            DISK_TOTAL.labels(mountpoint=partition.mountpoint).set(disk.total)
            DISK_USED.labels(mountpoint=partition.mountpoint).set(disk.used)
            DISK_FREE.labels(mountpoint=partition.mountpoint).set(disk.free)
            DISK_PERCENT.labels(mountpoint=partition.mountpoint).set(disk.percent)
        except PermissionError:
            continue
        
    # --- Сеть ---
    net_io = psutil.net_io_counters(pernic=True)
    for interface, io in net_io.items():
        # Игнорируем loopback интерфес
        if interface == 'lo':
            continue
        NET_BYTES_RECV.labels(interface=interface).set(io.bytes_recv)
        NET_BYTES_SENT.labels(interface=interface).set(io.bytes_sent)
        
    # --- Температура ---
    TEMPERATURE.set(get_temperature())
    
    # --- Аптайм ---
    uptime = time.time() - psutil.boot_time()
    UPTIME.set(uptime)


if __name__ == '__main__':
    PORT = 9101 # Порт по умолчанию для нашего экспортера
    
    print(f"Запуск NanoPi Exporter на порту {PORT}...")
    start_http_server(PORT)
    
    # Первый вызов для инициализации cpu_percent
    psutil.cpu_percent()
    
    # Бесконечный цикл обновления метрик
    while True:
        try:
            update_metrics()
        except Exception as e:
            print(f"Ошибка при обновлении метрик: {e}")
        # Обновляем каждые 5 секунд (Prometheus сам решает как часто делать скрейпинг, 
        # но мы держим актуальные данные в памяти)
        time.sleep(5)
