# ld2451

Driver Python para o radar **Hi-Link HLK-LD2451** (24 GHz, detecção de veículos) pela UART.
Roda na Raspberry Pi Zero (Raspberry Pi OS 32 bits, ARMv6) e em PCs Linux; Python 3.9–3.13;
única dependência: `pyserial`.

- Leitura contínua com ressincronização automática e timestamp de chegada
  (`CLOCK_BOOTTIME` no Linux, `monotonic` nas demais plataformas).
- Configuração segura: sessão sempre encerrada, timeout + retentativa, leitura de volta.
- Gravação e reprodução dos bytes brutos (`t_rx_ns,hex`).
- Testável sem hardware.

Protocolo e divergências em relação ao planejamento: [`docs/protocol.md`](docs/protocol.md).

## Ligação na Pi Zero

| Radar | Pi Zero (pino físico) |
| --- | --- |
| 5V | 2 (5 V) |
| GND | 6 (GND) |
| TX | 10 (GPIO15, RXD) |
| RX | 8 (GPIO14, TXD) |

Os níveis da UART do radar são TTL 3,3 V. Antes de usar:

```sh
sudo raspi-config   # Interface Options → Serial Port:
                    #   login shell pela serial? Não
                    #   hardware serial habilitado? Sim
sudo reboot
ls -l /dev/serial0
```

## Instalação

```sh
python3 -m pip install .            # ou: pip install -e '.[dev]' para desenvolvimento
```

## CLI

```sh
ld2451 monitor --port /dev/serial0          # alvos ao vivo (--json, --count N, --stats)
ld2451 info                                 # firmware, parâmetros e sensibilidade
ld2451 config --max-distance 30 --direction both --min-speed 3 --delay 2
ld2451 config --trigger-count 2 --snr-threshold 5
ld2451 config --set-baudrate 256000 --yes --restart   # persistente; depois use --baudrate 256000
ld2451 config --factory-reset --yes --restart
ld2451 record sessao.csv --duration 60
ld2451 replay sessao.csv [--realtime] [--json]
```

`-v` / `-vv` aumentam o nível de log. Não há comando de Bluetooth: o manual V1.03 não o documenta.

## API

```python
from ld2451 import LD2451, SerialTransport, DetectionParams, Direction

with LD2451(SerialTransport("/dev/serial0")) as radar:   # abre a porta e inicia a thread
    print(radar.get_firmware().version)                  # abre e fecha a sessão de configuração
    radar.set_params(DetectionParams(
        max_distance_m=30, direction=Direction.BOTH, min_speed_kmh=3, no_target_delay_s=2,
    ))                                                    # confere lendo de volta (verify=True)

    for report in radar.reports():                        # iterador bloqueante
        for t in report.targets:
            print(report.t_rx_ns, t.angle_deg, t.distance_m, t.speed_kmh, t.approaching)
```

Outras formas de consumo, para encaixar em executors e timers (por exemplo um nó ROS 2):

```python
radar = LD2451(SerialTransport("/dev/serial0"), clock=my_clock_ns)  # relógio injetável
radar.add_listener(lambda report: publish(report))   # callback na thread de leitura
radar.open(); radar.start()
report = radar.get_report(timeout=0.1)               # ou polling com timeout
print(radar.stats)                                   # contadores
radar.stop(); radar.close()                          # stop() termina em tempo limitado
```

Vários comandos numa única sessão:

```python
with radar.config_session() as info:      # 0x00FF na entrada, 0x00FE sempre na saída
    fw = radar.get_firmware()
    params = radar.get_params()
    sens = radar.get_sensitivity()
```

Comandos destrutivos exigem `confirm=True`: `set_baudrate(b, confirm=True)` e
`factory_reset(confirm=True)`; ambos só valem depois de `restart()`.

Reprodução offline, com os timestamps originais:

```python
from ld2451 import LD2451, ReplayTransport

with LD2451(ReplayTransport("sessao.csv"), reconnect=False) as radar:
    for report in radar.reports(timeout=0.5):
        ...
```

O pacote não faz nada no `import`: sem portas, threads, `print`, sinais ou configuração de
`logging` (o logger `ld2451` tem só um `NullHandler`). Não há estado global; várias instâncias
podem coexistir.

## Desenvolvimento

```sh
pip install -e '.[dev]'
pytest                       # sem hardware; testes @pytest.mark.hw ficam pulados
pytest --hw --port=/dev/serial0 tests/test_hw.py   # na Pi, com o radar ligado
ruff check . && ruff format --check . && mypy
tox                          # 3.9–3.13 + lint
```

Pendências que só o radar real confirma: [`docs/hardware-checks.md`](docs/hardware-checks.md).
