# perfid

7 Claude agents play Diplomacy with full private negotiation, isolated via GPG encryption.

## Project structure

```
perfid                  # Bash CLI — orchestrates game loop, single Docker container, sessions
game_state.py           # State init, save/load, starting positions, phase progression, sessions
message_router.py       # Route encrypted messages between agents
prompt.py               # System prompts + per-turn context for each agent
Makefile                # Convenience targets wrapping the CLI
TODO.md                 # Task tracking
```

Runtime game data lives outside this repo in `perfid-games/<game-id>/`.

## Isolation model

All agents run in a single Docker container with per-turn GPG key injection. Each power's private key is encrypted at rest using the GM's GPG public key (`gm@perfid.local`, stored in `$gd/gm-gpg/`). Before each turn, the orchestrator decrypts the key with the GM's private key and injects it into an ephemeral GNUPGHOME, then removes it after the turn. Public keys are published to `perfid-games/<game-id>/pubkeys/`.

## Game phases

Each game year has 5 phases:

1. **Spring** — free-form turn: negotiate + submit movement orders
2. **Spring Retreat** — retreat or disband dislodged units
3. **Fall** — free-form turn: negotiate + submit movement orders
4. **Fall Retreat** — retreat or disband
5. **Winter Adjustment** — build new units or disband excess

Spring and Fall are **free-form turns**: each power takes sequential turns where they can send messages, use jDip to simulate, or submit orders. Once a power submits orders, it's done for the season.

## Session persistence

Each power gets a persistent Claude session (stored in a Docker named volume). The first call uses `--session-id`, subsequent calls use `--resume` to maintain conversation context across turns.

## Game flow

```
perfid new         → create game dir, single container, GM keys
perfid bootstrap   → generate GPG keys for all powers (on host)
perfid play        → main loop: free-form turns → adjudicate → check win
perfid status      → print standings
perfid destroy     → tear down container and volumes
```

## Dev commands

```bash
make test          # run tests
make new           # perfid new
make play          # perfid play
make status        # perfid status
```

## Stack

- Bash for orchestration
- Python 3 for game logic
- jDip (Java) for DATC-compliant adjudication
- GPG for encryption
- Docker for single agent container (claude --session-id / --resume)
