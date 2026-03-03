# perfid

7 Claude agents play Diplomacy with full private negotiation, isolated via GPG encryption.

## Project structure

```
perfid                  # Python CLI — entry point (runs inside container)
game_loop.py            # Game orchestration: turn loop, GPG setup/cleanup, agent calls
game_state.py           # State init, save/load, starting positions, phase progression
message_router.py       # Route encrypted messages between agents
prompt.py               # System prompts + per-turn context for each agent
orders.py               # Order parsing, submission (dropbox-based), collection
jdip_adapter.py         # DATC-compliant adjudication via jDip (Java)
gpg.py                  # GPG helpers: key generation, encrypt/decrypt, player key management
logger.py               # Structured JSONL logging with timestamps
decrypt_log.py          # Post-game log decryption helper (decrypts encrypted fields)
Makefile                # Container build/run targets, test targets
Dockerfile.player       # Container image: node + python + java + GPG + claude
```

Runtime game data lives outside this repo in `perfid-games/<game-id>/`.

## Architecture

perfid runs **inside** the Docker container as root. The Makefile handles container creation/launch. No Docker subprocess calls from Python.

- perfid runs as **root** (manages GPG keys, file permissions)
- Agents run as **player** via `subprocess.run(['sudo', '-u', 'player', 'claude', ...])`
- jDip runs directly (Java is in the container)
- GPG operations use `gpg.py` directly (no docker exec)

## Isolation model

All agents run in a single Docker container with per-turn GPG key injection. Each power's private key is encrypted at rest using the GM's GPG public key (`gm@perfid.local`, stored in `$gd/gm-gpg/`). Before each turn, the orchestrator decrypts the key with the GM's private key and injects it into an ephemeral GNUPGHOME, then removes it after the turn. Public keys are published to `perfid-games/<game-id>/pubkeys/`.

The entire `~player/.claude/` directory is encrypted between turns with the power's key, preventing cross-power information leakage.

## Blind order submission

Orders use per-power dropboxes (`/tmp/orders-{power}/`, owned by player, mode 700). The main orders directory is root-only (chmod 700). Agents cannot see who has submitted. The game loop (root) collects from dropboxes into the real orders directory.

## Turn order and negotiation

Each round, powers are shuffled (`random.shuffle`) to prevent positional advantage. Minimum 3 negotiation rounds before order submission is allowed. All powers keep playing every round, even after submitting orders, to prevent inference about submission status.

## Game phases

Each game year has up to 5 phases:

1. **Spring** — 3+ negotiation rounds, then order submission rounds
2. **Spring Retreat** — retreat or disband dislodged units (skipped if none)
3. **Fall** — 3+ negotiation rounds, then order submission rounds
4. **Fall Retreat** — retreat or disband (skipped if none)
5. **Winter Adjustment** — build new units or disband excess

## Session persistence

Each power gets a persistent Claude session. The first call uses `--session-id`, subsequent calls use `--resume` to maintain conversation context across turns.

## Encrypted game log

All agent output is encrypted with the GM key and logged as JSONL. Use `decrypt_log.py` for post-game analysis:

```bash
python3 decrypt_log.py --gm-key gm-gpg/ game.jsonl > decrypted.jsonl
```

## Game flow

```
make new GAME=myid      → create game dir, GM keys, player keys
make play GAME=myid     → main loop: negotiate → orders → adjudicate → check win
make status GAME=myid   → print standings
make destroy GAME=myid  → tear down volumes, optionally delete game dir
```

## Dev commands

```bash
make test               # run all tests in Docker
make test-local         # run unit tests locally (GPG tests need short paths)
make test-full-game     # integration test with mock claude in Docker
make lint               # ruff check in Docker
make lint-local         # ruff check locally
```

## Stack

- Python 3 for orchestration and game logic
- jDip (Java) for DATC-compliant adjudication
- GPG for encryption
- Docker for container isolation (claude runs as unprivileged player user)
