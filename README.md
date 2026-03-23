# NanoPi Neo Plus 2 Prometheus Exporter

Легковесный Prometheus экспортёр, написанный на Python, специально адаптированный для сбора метрик с платы NanoPi Neo Plus 2 под управлением Armbian.

## Собираемые метрики
- **CPU**: Загрузка (%), частота (MHz), количество ядер.
- **Память**: Доступная, использованная (%), всего (байт).
- **Диск**: Использование всех примонтированных разделов (кроме виртуальных), свободное/занятое место.
- **Сеть**: Количество принятых и отправленных байт по интерфейсам.
- **Система**: Температура SoC (опрашивает специфичные для Armbian пути), аптайм.

## Установка на Nano Pi Neo Plus 2

Вы можете напрямую перенести эти файлы на вашу плату (например, через `scp`). Дальнейшие шаги выполняются на самой плате NanoPi через SSH.

### 1. Подготовка окружения
Склонируйте или скопируйте файлы в директорию `/opt/nanopi_exporter` (или любую другую, но тогда поменяйте пути в `.service` файле).

```bash
# Переходим в /opt и создаем директорию
sudo mkdir -p /opt/nanopi_exporter
# (Скопируйте файлы nanopi_exporter.py, requirements.txt и nanopi_exporter.service в эту папку)
cd /opt/nanopi_exporter
```

Установите `python3-venv`, если он еще не установлен:
```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
```

### 2. Создание виртуального окружения и установка зависимостей
Так как новые версии Armbian (Debian/Ubuntu) запрещают установку пакетов через `pip` в систему, лучше использовать `venv`.

```bash
sudo python3 -m venv venv
sudo ./venv/bin/pip install -r requirements.txt
```

### 3. Настройка автозапуска (Systemd)
Скопируйте сервис-файл в директорию systemd для автозапуска:

```bash
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
curl http://localhost:9101/
```

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
