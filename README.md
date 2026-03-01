# perfid

7 Claude agents play Diplomacy with full private negotiation, isolated via GPG encryption.

Each agent runs in a Docker container with its own GPG key pair.
Private keys stay inside the container; public keys are shared.
All inter-agent communication is GPG-encrypted — agents can see every file but
can only decrypt what's addressed to them.

## Requirements

- Docker
- Claude CLI (`claude`)
- Python 3
- Java (JRE) — for jDip adjudication
- GnuPG

## Quick start

```bash
# Create a new game with 7 agent containers
./perfid new my-game

# Agents generate GPG keys and publish public keys
./perfid bootstrap my-game

# Main loop: negotiate → orders → adjudicate → check win
./perfid play my-game

# Print current standings
./perfid status my-game

# Tear down containers when done
./perfid destroy my-game
```

## Make targets

```bash
make test                  # run tests in Docker
make test-local            # run tests locally
make lint                  # lint in Docker
make new GAME=my-game      # perfid new
make play GAME=my-game     # perfid play
make status GAME=my-game   # perfid status
make destroy GAME=my-game  # perfid destroy
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `PERFID_GAMES_DIR` | `perfid-games` | Directory for game data |
| `PERFID_NEGOTIATION_ROUNDS` | `3` | Rounds of negotiation per phase |
| `PERFID_PLAYER_IMAGE` | `perfid-player` | Docker image for agent containers |

Game data lives outside this repo in `$PERFID_GAMES_DIR/<game-id>/`.

## Game flow

Each turn follows this sequence:

1. **Diplomacy** — agents exchange encrypted messages over multiple negotiation rounds
2. **Movement** — agents submit orders (encrypted to the GM key)
3. **Adjudication** — jDip resolves orders per DATC rules
4. **Retreat** — dislodged units retreat or disband (if applicable)
5. **Adjustment** — builds/disbands in Winter (if applicable)
6. **Win check** — first to 18 supply centers wins

## Stack

- Bash — orchestration
- Python 3 — game logic (state, orders, messages, prompts)
- jDip (Java) — DATC-compliant adjudication
- GPG — encryption
- Docker — agent isolation (each runs `claude -p`)
