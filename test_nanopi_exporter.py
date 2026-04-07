import os
import tempfile

import nanopi_exporter as exp


def test_parse_csv_list():
    assert exp._parse_csv_list("a,b, c") == ("a", "b", "c")
    assert exp._parse_csv_list("") == ()
    assert exp._parse_csv_list("  ") == ()


def test_get_temperature_sysfs(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "temp")
        with open(p, "w") as f:
            f.write("42000\n")

        sysfs_path = "/sys/class/thermal/thermal_zone0/temp"
        monkeypatch.setattr(exp.os.path, "exists", lambda path: path == sysfs_path)

        import builtins

        real_open = builtins.open

        def _open(path, mode="r", *args, **kwargs):
            if path == sysfs_path:
                return real_open(p, mode, *args, **kwargs)
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", _open, raising=True)
        assert exp.get_temperature() == 42.0


def test_network_delta_does_not_decrease(monkeypatch):
    class IO:
        def __init__(self, recv, sent):
            self.bytes_recv = recv
            self.bytes_sent = sent

    calls = [
        {"eth0": IO(100, 200), "lo": IO(1, 1)},
        {"eth0": IO(150, 260), "lo": IO(2, 2)},
        {"eth0": IO(10, 20), "lo": IO(3, 3)},  # reset
        {"eth0": IO(30, 25), "lo": IO(4, 4)},
    ]
    it = iter(calls)
    monkeypatch.setattr(exp.psutil, "net_io_counters", lambda pernic=True: next(it))

    # Ensure we start clean for this test
    exp._last_net.clear()

    # Run updates; should not throw and should not attempt negative inc
    exp.update_metrics(skip_fstypes=(), ignore_ifaces=("lo",))
    exp.update_metrics(skip_fstypes=(), ignore_ifaces=("lo",))
    exp.update_metrics(skip_fstypes=(), ignore_ifaces=("lo",))
    exp.update_metrics(skip_fstypes=(), ignore_ifaces=("lo",))

