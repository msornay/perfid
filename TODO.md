# perfid TODO

TEST MUST BE LAUNCHED IN DOCKER CONTAINERS

## 2. jDip setup (source of truth for all game state)
- [ ] Download/vendor jDip headless JAR
- [ ] Figure out jDip headless CLI: new game, export state, submit orders, adjudicate
- [ ] `game_state.py` — thin adapter: call jDip, translate to/from our state.json
- [ ] Create game via jDip (standard variant, starting positions come from jDip)
- [ ] Export state from jDip → our JSON format for agents
- [ ] Submit orders to jDip
- [ ] Run adjudication via jDip, parse results
- [ ] Phase progression wrapper (jDip handles the actual game logic)
- [ ] Win condition check (read SC counts from jDip state)
- [ ] DATC compliance smoke tests (bounce, cut support, convoy paradox)

## 4. GPG key bootstrap
- [x] GM key generation (host-side, private key outside shared workspace)
- [x] Agent key generation (inside Docker sandbox, private key stays in sandbox)
- [x] Public key publishing to `perfid-games/<game-id>/pubkeys/`
- [x] Encrypt helper: `gpg_encrypt(plaintext, recipient_pub_key) → ciphertext`
- [x] Decrypt helper: `gpg_decrypt(ciphertext, private_key) → plaintext`
- [x] Key trust / import logic so agents can encrypt to GM and to each other

## 5. Agent I/O
- [x] Order submission: agent writes orders, encrypts with GM pub key, writes `.gpg`
- [x] Order decryption: GM decrypts all orders for adjudication
- [x] Order validation: check submitted orders are legal before adjudication
- [x] Private notes: agent encrypts with own pub key, reads back next turn

## 6. Message routing (`message_router.py`)
- [ ] Agent writes message encrypted with recipient's pub key → outbox
- [ ] Router moves message from outbox to recipient's inbox path
- [ ] Message naming: `<sender>-to-<recipient>-<phase>-r<round>-<seq>.gpg`
- [ ] List inbox for a given agent/phase/round
- [ ] Negotiation round management (N configurable rounds per negotiation phase)

## 7. Agent prompts (`prompt.py`)
- [x] System prompt: Diplomacy rules, GPG usage, file layout, phase instructions
- [x] Per-turn context: current state, inbox messages, previous results
- [x] Negotiation prompt: read inbox, compose & encrypt messages, strategy
- [x] Order prompt: analyze position, submit encrypted orders
- [x] Retreat prompt: choose retreat destinations or disband
- [x] Adjustment prompt: choose builds/disbands for winter
- [x] Bootstrap prompt: generate GPG key, publish public key

## 8. Bash CLI + game loop (`perfid`)
- [ ] `perfid new` — create game dir, spin up 7 Docker sandboxes, generate GM keys
- [ ] `perfid bootstrap` — run each agent to generate keys and publish pub keys
- [ ] `perfid play` — main loop: negotiate → orders → adjudicate → repeat
- [ ] `perfid status` — print current standings (SCs per power, units, year/phase)
- [ ] `perfid destroy` — tear down sandboxes, optionally clean game dir
- [ ] Parallel agent execution (7 agents via `claude -p` in Docker)
- [ ] Phase dispatch: negotiation rounds vs. order submission vs. retreat vs. adjust
- [ ] Error handling: agent timeout, malformed orders (default to Hold)
- [ ] Game dir structure creation (`pubkeys/`, `orders/`, `messages/`, `results/`)

## 9. Logging
- [x] Append-only `log.jsonl` in game dir
- [x] Log events: phase start, orders submitted, adjudication results, messages sent
- [x] Include timestamps, phase labels, acting power

## 10. Testing
- [ ] Unit tests for game state (phase progression, SC update, win check)
- [x] Unit tests for GPG (encrypt/decrypt round-trip, key exchange, isolation)
- [ ] Unit tests for message routing (inbox listing)
- [ ] Integration test: full turn cycle (negotiate → order → adjudicate)
- [ ] jDip adapter tests with known DATC cases

## 11. Polish
- [ ] CLAUDE.md with dev instructions
- [ ] Makefile targets: `new`, `bootstrap`, `play`, `status`, `destroy`, `test`
- [ ] README.md with project overview (if requested)
