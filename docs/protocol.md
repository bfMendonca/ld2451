# Protocolo serial do HLK-LD2451

Fonte da verdade: `docs/vendor/HLK-LD2451_Serial_Protocol_V1.03_EN.pdf`
(ver `docs/vendor/SOURCES.md`). Páginas abaixo referem-se a esse PDF.

## Camada física

- UART TTL, 115200 baud (padrão), 8 bits, sem paridade, 1 stop bit (p. 3).
- Todos os campos multi-byte são little-endian (p. 3).

## Frames

| Tipo | Cabeçalho | Comprimento | Corpo | Rodapé | Página |
| --- | --- | --- | --- | --- | --- |
| Comando (host → radar) | `FD FC FB FA` | 2 B LE | palavra de comando (2 B) + valor (N B) | `04 03 02 01` | 3 |
| ACK (radar → host) | `FD FC FB FA` | 2 B LE | palavra de comando \| `0x0100` (2 B) + retorno (N B) | `04 03 02 01` | 3 |
| Relatório de dados | `F4 F3 F2 F1` | 2 B LE | ver abaixo | `F8 F7 F6 F5` | 8 |

O primeiro campo do retorno de todo ACK é o status (2 B): `0` sucesso, `1` falha.

## Comandos

Toda configuração exige enviar antes "habilitar configuração" e, ao final,
"encerrar configuração" (p. 3, 10).

| Comando | Código | Valor enviado | Retorno no ACK (após status) | Exemplo envio | Exemplo ACK | Página |
| --- | --- | --- | --- | --- | --- | --- |
| Habilitar configuração | `0x00FF` | `01 00` | versão do protocolo (2 B, `0x0001`) + buffer (2 B) | `FD FC FB FA 04 00 FF 00 01 00 04 03 02 01` | `FD FC FB FA 08 00 FF 01 00 00 01 00 00 00 04 03 02 01` | 3–4 |
| Encerrar configuração | `0x00FE` | — | — | `FD FC FB FA 02 00 FE 00 04 03 02 01` | `FD FC FB FA 04 00 FE 01 00 00 04 03 02 01` | 4 |
| Gravar parâmetros de detecção | `0x0002` | 4 B: dist. máx. (m, `0x0A`–`0xFF`), direção (`00` só afastando, `01` só aproximando, `02` ambos), vel. mín. (km/h, `0x00`–`0x78`), atraso sem alvo (s, `0x00`–`0xFF`) | — | `FD FC FB FA 06 00 02 00 64 01 05 02 04 03 02 01` | `FD FC FB FA 04 00 02 01 00 00 04 03 02 01` | 4–5 |
| Ler parâmetros de detecção | `0x0012` | — | 4 B, mesmo formato de `0x0002` | `FD FC FB FA 02 00 12 00 04 03 02 01` | `FD FC FB FA 08 00 12 01 00 00 64 01 05 02 04 03 02 01` | 5 |
| Gravar sensibilidade | `0x0003` | 4 B: nº de disparos consecutivos (`1`–`0x0A`), limiar de SNR (`0` = padrão 4; `3`–`8`, maior = menos sensível), estendido (`00`), estendido (`00`) | — | `FD FC FB FA 06 00 03 00 02 08 00 00 04 03 02 01` | `FD FC FB FA 04 00 03 01 00 00 04 03 02 01` | 5–6 |
| Ler sensibilidade | `0x0013` | — | 4 B, mesmo formato de `0x0003` | `FD FC FB FA 02 00 13 00 04 03 02 01` | `FD FC FB FA 08 00 13 01 00 00 01 00 00 00 04 03 02 01` | 6 |
| Ler versão de firmware | `0x00A0` | — | tipo (2 B, `0x2451`) + versão principal (2 B) + versão secundária (4 B) | `FD FC FB FA 02 00 A0 00 04 03 02 01` | `FD FC FB FA 0C 00 A0 01 00 00 51 24 01 01 10 15 05 24 04 03 02 01` (ver divergência 3) | 6 |
| Definir baud rate | `0x00A1` | 2 B: índice (`1`=9600, `2`=19200, `3`=38400, `4`=57600, `5`=115200 padrão, `6`=230400, `7`=256000, `8`=460800) | — | `FD FC FB FA 04 00 A1 00 07 00 04 03 02 01` | `FD FC FB FA 04 00 A1 01 00 00 04 03 02 01` | 6–7 |
| Restaurar padrão de fábrica | `0x00A2` | — | — | `FD FC FB FA 02 00 A2 00 04 03 02 01` | `FD FC FB FA 04 00 A2 01 00 00 04 03 02 01` | 7 |
| Reiniciar módulo | `0x00A3` | — | — | `FD FC FB FA 02 00 A3 00 04 03 02 01` | `FD FC FB FA 04 00 A3 01 00 00 04 03 02 01` | 8 |

Notas:

- Baud rate e padrão de fábrica são gravados de forma persistente e só valem
  após reiniciar o módulo (p. 6–7).
- O reinício acontece logo após o ACK (p. 8); o radar sai do modo de
  configuração, então o driver não envia `0x00FE` depois dele.
- O manual não diz se parâmetros de detecção e sensibilidade valem sem
  reiniciar. Pendência de hardware (ver `docs/hardware-checks.md`).
- Versão de firmware: o exemplo `01 01` + `10 15 05 24` corresponde a
  `V1.01.24051510`, ou seja, `V{principal >> 8}.{principal & 0xFF:02x}.{secundária:08x}`.

## Relatório de dados (p. 8–9)

Corpo do frame `F4 F3 F2 F1`:

| Offset | Tam. | Campo |
| --- | --- | --- |
| 0 | 1 | número de alvos |
| 1 | 1 | alarme: `01` há alvo se aproximando, `00` não há |
| 2 + 5k | 1 | ângulo: graus = valor − `0x80` |
| 3 + 5k | 1 | distância (m, 0–100) |
| 4 + 5k | 1 | direção: `00` aproximando, `01` afastando |
| 5 + 5k | 1 | velocidade (km/h, 0–120) |
| 6 + 5k | 1 | SNR (0–255) |

Comprimento 0 (`F4 F3 F2 F1 00 00 F8 F7 F6 F5`) = sem alvo. O driver aceita
até 5 alvos (comprimento máximo 27) e calcula o número de alvos como
`(comprimento − 2) / 5`, sem depender do byte 0.

Exemplo do manual (p. 9), três alvos:

```
F4 F3 F2 F1 11 00 03 01 8A 28 00 3C 15 8A 1E 01 3C 0F 76 5F 00 3C 0F F8 F7 F6 F5
```

| Alvo | Ângulo | Distância | Direção | Velocidade | SNR |
| --- | --- | --- | --- | --- | --- |
| 1 | +10° | 40 m | aproximando | 60 km/h | 21 |
| 2 | +10° | 30 m | afastando | 60 km/h | 15 |
| 3 | −10° | 95 m | aproximando | 60 km/h | 15 |

## Divergências entre o planejamento e o manual

O manual vence em todos os casos.

1. **Direção do alvo.** O planejamento citava "sentido 01/00" sem ordem. O
   manual define `00` = aproximando e `01` = afastando (tabela e exemplo,
   p. 9). `Target.approaching` é `True` para `00`.
2. **Ordem dos 2 bytes iniciais do relatório.** A tabela 9 lista
   "quantidade de alvos, alarme", mas a análise do exemplo cita o alarme
   primeiro. Os bytes do exemplo (`03 01` com três alvos) confirmam a ordem
   da tabela: byte 0 = quantidade, byte 1 = alarme.
3. **Comprimento do ACK de firmware.** O exemplo (p. 6) traz comprimento
   `0B 00` (11), mas os bytes do corpo somam 12 (`A0 01 00 00 51 24 01 01 10 15 05 24`).
   Tratamos como erro de digitação: o teste golden usa `0C 00`.
   **Confirmado no hardware (2026-10-20):** o radar (firmware V1.13.25070114)
   responde com `0C 00`; o ACK decodifica sem erro.
4. **Bluetooth e MAC.** O planejamento previa `set_bluetooth` e leitura de
   MAC (faixa `0x00A0`–`0x00A5` de outros LD24xx). O V1.03 do LD2451 não
   documenta esses comandos; pela regra "comandos fora do manual não são
   implementados", foram retirados da API e da CLI (`--bluetooth`).
5. **Exemplo do relatório com OCR.** O PDF mostra `IE` no segundo alvo;
   pelo contexto (30 m) é `1E`.
6. **Modo de engenharia.** O manual menciona um modo de engenharia com
   energias por faixa (p. 8), mas não documenta como ativá-lo nem o
   formato. Não implementado.
7. **Sem itens `UNVERIFIED`.** Todos os comandos do planejamento que
   continuam no driver estão no manual com código e exemplo.
   `UnverifiedCommandWarning` continua existindo e é emitido quando se
   chama `send_command` com um código fora de `CommandCode`.

## Observado no hardware (2026-10-20)

Radar com firmware `V1.13.25070114` (tipo `0x2451`), mais novo que o V1.01 do
manual, ligado a uma Pi Zero em `/dev/serial0`.

- Todos os comandos usados pelo driver (`0x00FF`, `0x00FE`, `0x0002`, `0x0012`,
  `0x0003`, `0x0013`, `0x00A0`) respondem na primeira tentativa, em 10–60 ms.
- ACK de habilitar configuração: versão do protocolo `0x0001`, buffer `0x0000`.
- Sem alvo, o radar envia um frame vazio (`F4 F3 F2 F1 00 00 F8 F7 F6 F5`)
  a cada ~1,39 s.
- Configuração de fábrica deste exemplar: distância máx. 100 m, só
  aproximando, vel. mín. 1 km/h, atraso 1 s; disparos 2, limiar de SNR 0
  (padrão).
- Captura real em `tests/fixtures/real_idle.csv`.

## Limitação conhecida do decodificador

Um cabeçalho falso ou truncado com comprimento plausível (até 27 em dados, 64
em comandos) faz o decodificador esperar esse número de bytes antes de
descartar o frame e ressincronizar. Sem alvo (10 bytes a cada ~1,39 s) isso
pode atrasar a entrega de 3 a 10 s. Nenhum frame válido se perde e os
timestamps continuam corretos.
