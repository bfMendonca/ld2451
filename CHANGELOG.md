# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/);
versionamento semântico.

## [Unreleased]

### Adicionado

- `LD2451(drop_oldest=False)`: a thread de leitura espera o consumidor em vez de descartar
  relatórios. Usado por `ld2451 replay`.

### Corrigido

- `ld2451 replay` perdia relatórios em logs com mais de ~256 frames.
- `LD2451.running` podia lançar `AttributeError` em corrida com `stop()`.
- Comandos chamados de um listener travavam a thread de leitura; agora falham na hora com
  `ConfigSessionError`. `stop()`/`close()` funcionam dentro de um listener.
- `monitor --stats` e `replay --stats` imprimem os contadores também com Ctrl+C.
- `config` valida os valores antes de abrir a porta e recusa `--factory-reset` junto com
  outras alterações.
- Backoff da reconexão continua crescendo quando a porta reabre e falha de novo.
- `restart()` não é mais repetido (o módulo já está reiniciando).
- `Recorder` é thread-safe; `SerialTransport.write` não chama mais `flush()` (podia bloquear).

## [0.1.0] - 2026-10-19

### Adicionado

- `FrameDecoder` com ressincronização, limites de comprimento e buffer, e timestamp por frame.
- Codificação/decodificação de todos os comandos do manual V1.03 e do relatório de alvos.
- Classe `LD2451`: thread de leitura, fila e callbacks, sessão de configuração, timeout e
  retentativa, verificação após escrita, reconexão com backoff, contadores.
- Transportes `SerialTransport`, `ReplayTransport` e `LoopbackTransport`.
- Gravação/reprodução de bytes brutos (`t_rx_ns,hex`).
- CLI `ld2451 monitor | info | config | record | replay`.
