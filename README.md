# NanoPi Neo Plus 2 Prometheus Exporter

Легковесный Prometheus экспортёр, написанный на Python, специально адаптированный для сбора метрик с платы NanoPi Neo Plus 2 под управлением Armbian.

## Собираемые метрики
- **CPU**: Общая загрузка (%), загрузка по ядрам (%), частота (MHz) общая и по ядрам, количество ядер.
- **Память**: Доступная, использованная (%), всего (байт).
- **Диск**: Использование всех примонтированных разделов (кроме виртуальных), свободное/занятое место, inode usage, disk I/O (bytes/ops).
- **Сеть**: Bytes/packets RX/TX, ошибки/дропы, состояние интерфейса (up), MTU, скорость (если доступно через sysfs).
- **Система**: Температура SoC и thermal zones, load average (1/5/15), аптайм.
- **Экспортёр**: Длительность сбора метрик, счётчик ошибок по компонентам, ready/self-metrics.

## Установка на Nano Pi Neo Plus 2

Вы можете напрямую перенести эти файлы на вашу плату (например, через `scp`). Дальнейшие шаги выполняются на самой плате NanoPi через SSH.

### 1. Подготовка окружения
Установите `git`, если он ещё не установлен, и склонируйте этот репозиторий прямо в `/opt/nanopi_exporter` (это стандартный путь, если захотите другую папку — не забудьте поменять её в `.service` файле).

```bash
# Обновляем систему и ставим нужные пакеты
sudo apt update
sudo apt install -y git python3-venv python3-pip python3-dev gcc

# Переходим в /opt и клонируем репозиторий
cd /opt
sudo git clone https://github.com/iwizard7/nanopi_exporter.git

# Переходим в папку с экспортёром
cd /opt/nanopi_exporter
```

### 2. Создание виртуального окружения и установка зависимостей
Так как новые версии Armbian (Debian/Ubuntu) запрещают установку пакетов через `pip` глобально, мы используем изолированное окружение (`venv`).

```bash
sudo python3 -m venv venv
sudo ./venv/bin/pip install -r requirements.txt
```

### 3. Настройка автозапуска (Systemd)
Скопируйте сервис-файл в директорию systemd для автозапуска:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin nanopi-exporter
sudo cp nanopi_exporter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nanopi_exporter
sudo systemctl start nanopi_exporter
```

### 4. Проверка работы
Убедитесь, что сервис работает без ошибок:
```bash
sudo systemctl status nanopi_exporter
```

Чтобы посмотреть сами метрики, сделайте запрос:
```bash
curl http://localhost:9101/metrics
```

Дополнительно доступны health endpoints:

- `GET /healthz` — всегда `200 ok`, если процесс жив
- `GET /readyz` — `200 ready`, если сбор метрик успешно выполнялся недавно (иначе `503`)

## Настройка Prometheus сервера
На вашем сервере с Prometheus добавьте следующий job в файл конфигурации `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: 'nanopi'
    static_configs:
      - targets: ['<IP_АДРЕС_NANOPI>:9101']
```
(Замените `<IP_АДРЕС_NANOPI>` на реальный IP адрес вашей платы в локальной сети).

Перезапустите ваш Prometheus сервер, и метрики начнут собираться!

## Настройка (опционально)
Экспортёр поддерживает настройку через переменные окружения или CLI-флаги:

- **`NANOPI_EXPORTER_PORT` / `--port`**: порт (по умолчанию `9101`)
- **`NANOPI_EXPORTER_BIND` / `--bind`**: адрес биндинга (по умолчанию `0.0.0.0`)
- **`NANOPI_EXPORTER_INTERVAL_SECONDS` / `--interval-seconds`**: интервал обновления метрик (по умолчанию `5`)
- **`NANOPI_EXPORTER_SKIP_FSTYPES` / `--skip-fstypes`**: игнорируемые типы ФС, через запятую
- **`NANOPI_EXPORTER_IGNORE_IFACES` / `--ignore-ifaces`**: игнорируемые интерфейсы, через запятую (по умолчанию `lo`)
- **`NANOPI_EXPORTER_LOG_LEVEL` / `--log-level`**: уровень логов (`INFO`, `DEBUG`, ...)
- **`NANOPI_EXPORTER_READY_MAX_AGE_SECONDS` / `--ready-max-age-seconds`**: максимальный “возраст” последнего успешного сбора для `/readyz` (по умолчанию `30`)
