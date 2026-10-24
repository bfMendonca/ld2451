# Pendências que só o radar real confirma

Rodar na Pi Zero com o radar ligado (ver ligação no README). Instalar antes:
`pip install -e '.[dev]'`.

Status em 2026-10-20 (firmware V1.13.25070114): ✅ confirmado, ⏳ pendente.

| # | Status | O que confirmar | Comando |
| --- | --- | --- | --- |
| 1 | ✅ | Frames de dados chegam e decodificam sem rodapé inválido | `pytest --hw --port=/dev/serial0 tests/test_hw.py::test_receives_reports` |
| 2 | ✅ | ACK de firmware: comprimento real (`0C 00` ou `0B 00` como no manual) e tipo `0x2451` | `ld2451 -vv info 2>&1 \| grep "ACK 0x00A0"`; depois `pytest --hw tests/test_hw.py::test_firmware -s` |
| 3 | ✅ | Ida e volta dos parâmetros de detecção | `pytest --hw tests/test_hw.py::test_params_roundtrip` |
| 4 | ✅ | Ida e volta da sensibilidade | `pytest --hw tests/test_hw.py::test_sensitivity_roundtrip` |
| 5 | ✅ | O radar volta a reportar depois de sair do modo de configuração | `pytest --hw tests/test_hw.py::test_reports_resume_after_session` |
| 6 | ⏳ | Parâmetros valem sem reiniciar (o manual não diz) | `ld2451 config --max-distance 20` e, com um alvo a ~30 m, `ld2451 monitor --count 50`: não deve aparecer alvo além de 20 m |
| 7 | ⏳ | Direção `00` = aproximando (manual p. 9) | `ld2451 monitor` andando em direção ao radar: `approaching` |
| 8 | ⏳ | Ordem dos bytes 0/1 do relatório (quantidade, alarme) | `ld2451 monitor --json --count 20`: `alarm` deve ser `true` só com alvo se aproximando |
| 9 | ⏳ | Troca de baud rate e volta ao padrão | `ld2451 config --set-baudrate 230400 --yes --restart`; `ld2451 info --baudrate 230400`; `ld2451 config --baudrate 230400 --set-baudrate 115200 --yes --restart` |
| 10 | ⏳ | Reconexão: desligar e religar o radar durante `ld2451 monitor --stats` | `ld2451 -v monitor --stats`, desconectar 5 V por alguns segundos; deve logar "reconnected" (se a `/dev/serial0` não sumir, só os contadores mudam) |
| 11 | ✅ | Gravação e reprodução de uma sessão real | `ld2451 record tests/fixtures/real_session.csv --duration 30` e `ld2451 replay tests/fixtures/real_session.csv` |


Notas da execução de 2026-10-20:

- 1–5: `pytest --hw` 5/5 passaram; os parâmetros originais foram restaurados.
- 2: o radar envia comprimento `0C 00` (manual diz `0B 00`).
- 11: captura sem alvo salva em `tests/fixtures/real_idle.csv`.
- 6, 7 e 8 precisam de um alvo em movimento na frente do radar; 9 grava
  baud rate de forma persistente; 10 exige desligar o radar fisicamente.
