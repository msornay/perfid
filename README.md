# perfid

7 Claude agents play Diplomacy autonomously, with private negotiation enforced by GPG encryption.

Each agent is a Claude instance running inside a shared Docker container. Agents can read every file on the shared filesystem, but all communication and orders are GPG-encrypted — an agent can only decrypt messages addressed to it. Private keys are encrypted at rest and injected ephemerally per turn.

## How it works

### Game phases

Each game year cycles through up to five phases:

1. **Spring** — 3 negotiation rounds (messages only), then order submission rounds
2. **Spring Retreat** — dislodged units retreat or disband (skipped if none)
3. **Fall** — same structure as Spring
4. **Fall Retreat** — retreat or disband (skipped if none)
5. **Winter Adjustment** — build new units at home centers or disband excess

The first power to control 18 of the 34 supply centers wins.

### Negotiation rounds

During each movement phase, agents get at least 3 negotiation-only rounds before they can submit orders. In every round, all 7 powers are called in shuffled order. Each call lets the agent read its inbox, send encrypted messages, simulate strategies with jDip, and (after round 3) submit orders. All powers keep playing every round even after submitting, so no one can infer who has already locked in.

### Blind order submission

Orders use per-power dropboxes (`/tmp/orders-{power}/`, mode 700). The main orders directory is root-only. Agents cannot see who has submitted. The game loop (running as root) collects from dropboxes into the real orders directory after each agent's turn.

### GPG isolation model

At game creation, the GM generates a master GPG key pair. Each player's private key is generated in a temporary keyring, then encrypted with the GM's public key and stored as `keys/{power}.key.gpg`. Public keys are published to `pubkeys/`.

Before each turn:
1. The GM decrypts the player's private key into an ephemeral `GNUPGHOME`
2. All public keys are imported into that keyring
3. The agent runs with `GNUPGHOME` pointing to the ephemeral keyring
4. After the turn, the ephemeral keyring is destroyed

The agent's `~player/.claude/` directory (conversation memory) is also encrypted between turns with the power's key, preventing cross-power information leakage.

### Container architecture

- **perfid** runs as root inside the container — it manages GPG keys, file permissions, and the game loop
- **Agents** run as the unprivileged `player` user via `sudo -u player claude`
- **jDip** (Java) runs directly in-container for DATC-compliant order adjudication
- Agents can call `simulate()` from Python to test order combinations against jDip without modifying the game state

## Usage

```bash
make new GAME=myid        # Create game dir, GM keys, player keys
make play GAME=myid       # Main loop: negotiate -> orders -> adjudicate -> check win
make status GAME=myid     # Print current standings
make destroy GAME=myid    # Tear down volumes
```

## Post-game analysis

Agent output is encrypted with the GM key and logged as JSONL. To decrypt:

```bash
python3 decrypt_log.py --gm-key perfid-games/myid/gm-gpg/ perfid-games/myid/log.jsonl > decrypted.jsonl
```

## Development

```bash
make test               # Run all tests in Docker
make test-local         # Run unit tests locally
make test-full-game     # Integration test with mock claude in Docker
make lint               # Lint in Docker
```

## Stack

- Python 3 — orchestration, game logic, GPG helpers, prompt generation
- jDip (Java) — DATC-compliant Diplomacy adjudication
- GPG — per-agent encryption and key isolation
- Docker — single container with root orchestrator and unprivileged agent user
