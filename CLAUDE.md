# perfid

7 Claude agents play Diplomacy with full private negotiation, isolated via GPG encryption.

## Project structure

```
perfid                  # Bash CLI — orchestrates game loop, Docker containers, parallel agents
game_state.py           # State init, save/load, starting positions, phase progression
message_router.py       # Route encrypted messages between agents
prompt.py               # System prompts + per-turn context for each agent
Makefile                # Convenience targets wrapping the CLI
TODO.md                 # Task tracking
```

Runtime game data lives outside this repo in `perfid-games/<game-id>/`.

## Isolation model

Each agent runs in a Docker container with its own GPG key pair. Private keys stay inside the container (never in the shared workspace). Public keys are published to `perfid-games/<game-id>/pubkeys/`. All inter-agent communication is GPG-encrypted — agents can see every file but can only decrypt what's addressed to them.

## Game flow

```
perfid new         → create game dir, 7 Docker containers, GM keys
perfid bootstrap   → agents generate key pairs, publish pub keys
perfid play        → main loop: negotiate → orders → adjudicate → check win
perfid status      → print standings
perfid destroy     → tear down containers
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
- Docker for agent containers (claude -p)
