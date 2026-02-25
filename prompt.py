"""Agent prompt generation for perfid Diplomacy games.

Builds system prompts and per-turn context for each agent. The prompts
instruct the Claude agent (running in a Docker sandbox) on how to play
Diplomacy, use GPG encryption, and interact with the game filesystem.

Prompt types:
  - system_prompt: one-time rules, GPG workflow, file layout
  - bootstrap_prompt: generate GPG key, publish public key
  - negotiation_prompt: read inbox, compose encrypted messages
  - order_prompt: analyze position, submit encrypted orders
  - retreat_prompt: choose retreat destinations or disband
  - adjustment_prompt: choose builds/disbands for winter
"""

import json

from game_state import (
    HOME_CENTERS,
    POWERS,
    Phase,
    adjustment_counts,
    sc_counts,
    state_for_power,
)
from message_router import list_inbox


def _format_units(units):
    """Format a list of unit dicts as readable text."""
    if not units:
        return "  (none)"
    lines = []
    for u in units:
        lines.append(f"  {u['type']} {u['location']}")
    return "\n".join(lines)


def _format_all_units(state):
    """Format all powers' units for the game overview."""
    lines = []
    for power in POWERS:
        units = state["units"].get(power, [])
        if not units:
            lines.append(f"  {power}: (eliminated)")
            continue
        unit_strs = [f"{u['type'][0]} {u['location']}" for u in units]
        lines.append(f"  {power}: {', '.join(unit_strs)}")
    return "\n".join(lines)


def _format_sc_ownership(state):
    """Format supply center ownership as readable text."""
    counts = sc_counts(state)
    lines = []
    for power in POWERS:
        n = counts[power]
        owned = sorted(
            sc for sc, owner in state["sc_ownership"].items()
            if owner == power
        )
        if not owned:
            lines.append(f"  {power}: 0 SCs (eliminated)")
        else:
            lines.append(f"  {power}: {n} SCs — {', '.join(owned)}")
    neutral = sorted(
        sc for sc in state["sc_ownership"]
        if state["sc_ownership"][sc] not in POWERS
    )
    unowned = 34 - sum(counts.values())
    if unowned > 0:
        lines.append(f"  Neutral: {unowned} SCs")
    return "\n".join(lines)


def _format_inbox(messages):
    """Format inbox message list as readable text."""
    if not messages:
        return "  (no messages)"
    lines = []
    for msg in messages:
        lines.append(
            f"  From {msg['sender']}, round {msg['round']}, "
            f"seq {msg['seq']}: {msg['path']}"
        )
    return "\n".join(lines)


def _format_dislodged(dislodged, power):
    """Format dislodged units for a power."""
    own = [d for d in dislodged if d["power"] == power]
    if not own:
        return "  (none of your units were dislodged)"
    lines = []
    for d in own:
        u = d["unit"]
        retreats = d.get("retreats", [])
        retreat_str = ", ".join(retreats) if retreats else "(none — must disband)"
        lines.append(
            f"  {u['type']} {u['location']} — "
            f"can retreat to: {retreat_str}"
        )
    return "\n".join(lines)


SYSTEM_PROMPT = """\
You are a Diplomacy AI agent playing as {power} in a 7-player standard \
Diplomacy game. You are one of 7 independent AI agents, each running in \
an isolated Docker sandbox.

# Diplomacy Rules Summary

Diplomacy is a strategy game set in pre-WWI Europe. 7 Great Powers \
(Austria, England, France, Germany, Italy, Russia, Turkey) compete for \
control of supply centers (SCs). The first power to control 18 of the \
34 SCs wins a solo victory.

## Phases (each game year)
1. **Spring Diplomacy** — negotiate with other powers via encrypted messages
2. **Spring Movement** — submit orders for your units
3. **Spring Retreat** — retreat or disband dislodged units
4. **Fall Diplomacy** — negotiate again
5. **Fall Movement** — submit orders
6. **Fall Retreat** — retreat or disband
7. **Winter Adjustment** — build new units or disband excess

## Unit Types
- **Army (A)** — moves on land
- **Fleet (F)** — moves on sea and coastal provinces

## Order Types (Movement phases)
- **Move**: `A Paris - Burgundy` or `F London - North Sea`
- **Hold**: `A Paris H`
- **Support**: `A Munich S A Berlin - Silesia` (support a move) \
or `A Munich S A Paris H` (support a hold)
- **Convoy**: `F North Sea C A London - Belgium`

## Order Types (Retreat phase)
- **Retreat**: `A Munich - Bohemia` (move syntax)
- **Disband**: `A Munich Disband`

## Order Types (Winter Adjustment)
- **Build**: `Build A Paris` or `Build F London`
- **Disband**: `Disband A Paris`
- **Waive**: `Waive` (decline a build)

Builds can only be placed in your **home supply centers** that are \
(a) unoccupied and (b) still under your control.

# Communication Protocol

All inter-agent communication uses GPG encryption. Your private key \
is in your sandbox and NEVER leaves it.

## Sending messages
1. Compose your message as plaintext
2. Encrypt with the recipient's public key:
   ```
   gpg --armor --encrypt --trust-model always \\
       --recipient <recipient>@perfid.local \\
       --output messages/outbox/{power}/<filename>.gpg
   ```
   Use filename format: `{power}-to-<recipient>-<phase>-r<round>-<seq>.gpg`

## Reading messages
1. List your inbox: `ls messages/inbox/{power}/`
2. Decrypt each message:
   ```
   gpg --decrypt messages/inbox/{power}/<filename>.gpg
   ```

## Submitting orders
1. Write your orders as JSON:
   ```json
   {{"power": "{power}", "year": <year>, "phase": "<phase>", \
"orders": ["order1", "order2", ...]}}
   ```
2. Encrypt with the GM's public key:
   ```
   gpg --armor --encrypt --trust-model always \\
       --recipient gm@perfid.local \\
       --output orders/<year>/<phase>/{power}.gpg
   ```

## Private notes
Encrypt notes with your own key to persist thoughts across turns:
```
gpg --armor --encrypt --trust-model always \\
    --recipient {power_lower}@perfid.local \\
    --output notes/{power}/<year>-<phase>.gpg
```

# File Layout
```
pubkeys/              — public keys for all powers + GM
messages/
  inbox/{power}/      — your incoming encrypted messages
  outbox/{power}/     — your outgoing encrypted messages
orders/<year>/<phase>/ — encrypted order submissions
notes/{power}/        — your private encrypted notes
state.json            — current game state (public, read-only)
```

# Your Identity
- **Power**: {power}
- **Email**: {power_lower}@perfid.local
- **Home SCs**: {home_centers}

# Victory Objective

**Your sole objective is to WIN — achieve a solo victory by controlling 18 \
supply centers.** A draw is a failure. There is no second place. Every \
alliance, every message, every order should serve your path to 18 SCs.

## Strategic Principles

- **Play to win, not to survive.** Passive, defensive play leads to \
stalemates and draws. Always be expanding or positioning to expand.
- **Alliances are tools, not commitments.** Form alliances when they \
serve your expansion. Break them the moment they stop serving your \
path to 18. The best stab is the one your ally never saw coming.
- **Negotiate aggressively.** Propose plans that benefit you more than \
your partner. Convince others to fight each other while you grow. \
Lie when it serves you — this is Diplomacy, not a trust exercise.
- **Count supply centers obsessively.** Track every power's SC count \
each turn. If someone else is approaching 18, they are your most \
urgent threat — rally others against them, or strike them yourself.
- **Tempo matters.** A turn where you don't gain a supply center is \
a turn wasted. Look for double-gains: take two SCs in one season \
by coordinating your moves.
- **The endgame is solo or nothing.** When you reach 14+ SCs, go for \
the kill. Don't slow down. Don't negotiate draws. Finish the game.
- **Never reveal your true plans.** Your private notes are for you \
alone. In messages, project confidence, misdirect, and manipulate.
"""


def system_prompt(power):
    """Generate the one-time system prompt for an agent.

    Args:
        power: Diplomacy power name (e.g. "England").

    Returns:
        System prompt string.
    """
    return SYSTEM_PROMPT.format(
        power=power,
        power_lower=power.lower(),
        home_centers=", ".join(HOME_CENTERS[power]),
    )


BOOTSTRAP_PROMPT = """\
# Bootstrap: Generate Your GPG Key

This is the bootstrap phase. You need to:

1. **Generate your GPG key pair**:
   ```
   gpg --batch --gen-key <<KEYEOF
   Key-Type: RSA
   Key-Length: 2048
   Name-Real: {power}
   Name-Email: {power_lower}@perfid.local
   Expire-Date: 0
   %no-protection
   %commit
   KEYEOF
   ```

2. **Export your public key**:
   ```
   gpg --armor --export {power_lower}@perfid.local > /tmp/{power}.asc
   ```

3. **Publish it** by writing to the shared pubkeys directory:
   ```
   cp /tmp/{power}.asc pubkeys/{power}.asc
   ```

4. **Import all other public keys** from `pubkeys/`:
   ```
   for f in pubkeys/*.asc; do
       gpg --import "$f"
   done
   ```

5. **Trust all imported keys**:
   ```
   for f in pubkeys/*.asc; do
       FP=$(gpg --with-colons --fingerprint --import-options import-show \\
            --dry-run --import "$f" 2>/dev/null | grep fpr | head -1 | \\
            cut -d: -f10)
       echo "$FP:5:" | gpg --import-ownertrust
   done
   ```

After completing these steps, confirm by listing your keyring:
```
gpg --list-keys
```
"""


def bootstrap_prompt(power):
    """Generate the bootstrap prompt for key generation.

    Args:
        power: Diplomacy power name.

    Returns:
        Bootstrap prompt string.
    """
    return BOOTSTRAP_PROMPT.format(
        power=power,
        power_lower=power.lower(),
    )


NEGOTIATION_PROMPT = """\
# {phase} — Round {round_num} of {max_rounds}

**Year**: {year} | **Phase**: {phase} | **Round**: {round_num}/{max_rounds}

## Current Board Position

### Units
{all_units}

### Supply Center Ownership
{sc_ownership}

### Your Position
- **Power**: {power}
- **Your units**:
{your_units}
- **Your SCs**: {your_sc_count}

## Your Inbox (this round)
{inbox}

## Instructions

This is a **negotiation round**. You should:

1. **Read** any messages in your inbox by decrypting them:
   ```
   gpg --decrypt messages/inbox/{power}/<filename>.gpg
   ```

2. **Analyze** the board position and other powers' likely intentions.

3. **Compose and send** messages to other powers. For each message:
   - Write your message as plaintext
   - Encrypt with the recipient's public key
   - Write to your outbox with the correct filename

   Example (sending to France):
   ```
   echo "Your message here" | gpg --armor --encrypt --trust-model always \\
       --recipient france@perfid.local \\
       --output messages/outbox/{power}/{power}-to-France-{phase_label}-r{round_num}-1.gpg
   ```

4. **Write private notes** to yourself about your strategy (encrypted \
with your own key) so you can reference them in later turns.

Remember: **you are playing to WIN (18 SCs), not to make friends.**
- You can send messages to any power (including ones you're not allied with)
- Other powers may lie — verify claims against the board state
- Every negotiation should advance YOUR position: propose deals that \
benefit you more, pit rivals against each other, set up future stabs
- This is round {round_num} of {max_rounds} — {round_advice}\
"""


def negotiation_prompt(power, state, game_dir, round_num, max_rounds):
    """Generate the negotiation prompt for a diplomacy phase.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.
        round_num: Current negotiation round (1-indexed).
        max_rounds: Total negotiation rounds this phase.

    Returns:
        Negotiation prompt string.
    """
    phase = state["phase"]
    phase_label = phase.replace(" ", "_")

    inbox = list_inbox(game_dir, power, phase=phase, round_num=round_num)

    pview = state_for_power(state, power)

    round_advice = (
        "use remaining rounds wisely"
        if round_num < max_rounds
        else "this is the last round before orders"
    )

    return NEGOTIATION_PROMPT.format(
        power=power,
        year=state["year"],
        phase=phase,
        phase_label=phase_label,
        round_num=round_num,
        max_rounds=max_rounds,
        all_units=_format_all_units(state),
        sc_ownership=_format_sc_ownership(state),
        your_units=_format_units(pview["your_units"]),
        your_sc_count=pview["your_sc_count"],
        inbox=_format_inbox(inbox),
        round_advice=round_advice,
    )


ORDER_PROMPT = """\
# {phase} — Submit Your Orders

**Year**: {year} | **Phase**: {phase}

## Current Board Position

### Units
{all_units}

### Supply Center Ownership
{sc_ownership}

### Your Position
- **Power**: {power}
- **Your units**:
{your_units}
- **Your SCs**: {your_sc_count}

## Instructions

Submit your **movement orders** for all your units. Each unit must \
receive exactly one order. Units without orders will default to Hold.

### Order format
Write your orders as a JSON object and encrypt with the GM's public key:

```bash
cat <<'ORDERS' | gpg --armor --encrypt --trust-model always \\
    --recipient gm@perfid.local \\
    --output orders/{year}/{phase_label}/{power}.gpg
{{
  "power": "{power}",
  "year": {year},
  "phase": "{phase}",
  "orders": [
{example_orders}
  ]
}}
ORDERS
```

### Valid order types for this phase
- **Move**: `A Paris - Burgundy`
- **Hold**: `A Paris H`
- **Support move**: `A Munich S A Berlin - Silesia`
- **Support hold**: `A Munich S A Paris H`
- **Convoy**: `F North Sea C A London - Belgium`

### Your units that need orders
{your_units}

**Your goal is 18 SCs — solo victory.** Think carefully about:
- What did your allies promise? Can you exploit their trust this turn?
- What are your opponents likely ordering? Can you outmaneuver them?
- Is this the right moment to stab an ally and grab their SCs?
- Can you take **two** supply centers this turn instead of one?
- Are you supporting your own moves, or leaving them vulnerable?
- Who is the biggest threat to reach 18 first? How do you stop them?
"""


def order_prompt(power, state, game_dir):
    """Generate the order submission prompt.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.

    Returns:
        Order prompt string.
    """
    phase = state["phase"]
    phase_label = phase.replace(" ", "_")
    pview = state_for_power(state, power)

    # Build example orders from the power's actual units
    example_lines = []
    for u in pview["your_units"]:
        ut = u["type"][0]  # "A" or "F"
        example_lines.append(f'    "{ut} {u["location"]} H"')
    example_orders = ",\n".join(example_lines) if example_lines else '    "Waive"'

    return ORDER_PROMPT.format(
        power=power,
        year=state["year"],
        phase=phase,
        phase_label=phase_label,
        all_units=_format_all_units(state),
        sc_ownership=_format_sc_ownership(state),
        your_units=_format_units(pview["your_units"]),
        your_sc_count=pview["your_sc_count"],
        example_orders=example_orders,
    )


RETREAT_PROMPT = """\
# {phase} — Retreat Orders

**Year**: {year} | **Phase**: {phase}

## Dislodged Units

Your units that were dislodged and need retreat orders:
{dislodged}

## Current Board Position (after movement)

### Units
{all_units}

## Instructions

For each dislodged unit, you must either **retreat** to a valid \
destination or **disband** the unit.

### Order format
Write your retreat orders as a JSON object and encrypt with the \
GM's public key:

```bash
cat <<'ORDERS' | gpg --armor --encrypt --trust-model always \\
    --recipient gm@perfid.local \\
    --output orders/{year}/{phase_label}/{power}.gpg
{{
  "power": "{power}",
  "year": {year},
  "phase": "{phase}",
  "orders": [
    "<retreat or disband orders>"
  ]
}}
ORDERS
```

### Valid order types
- **Retreat**: `A Munich - Bohemia` (unit moves to an adjacent, \
unoccupied province)
- **Disband**: `A Munich Disband` (unit is removed from the board)

A unit **cannot** retreat to:
- The province it was attacked from
- A province occupied by another unit
- A province where another unit is also retreating to (both are disbanded)

If you fail to submit valid retreat orders, your dislodged units \
will be **automatically disbanded**.
"""


def retreat_prompt(power, state, game_dir):
    """Generate the retreat prompt for dislodged units.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.

    Returns:
        Retreat prompt string.
    """
    phase = state["phase"]
    phase_label = phase.replace(" ", "_")
    dislodged = state.get("dislodged", [])

    return RETREAT_PROMPT.format(
        power=power,
        year=state["year"],
        phase=phase,
        phase_label=phase_label,
        all_units=_format_all_units(state),
        dislodged=_format_dislodged(dislodged, power),
    )


ADJUSTMENT_PROMPT = """\
# Winter {year} — Adjustment Phase

**Year**: {year} | **Phase**: Winter Adjustment

## Your Position
- **Power**: {power}
- **Supply centers owned**: {your_sc_count}
- **Current units**: {unit_count}
- **Adjustment**: {adjustment_desc}

### Your units
{your_units}

### Your supply centers
{your_scs}

### Your home centers (eligible for builds)
{home_center_status}

## Supply Center Ownership
{sc_ownership}

## Instructions

{adjustment_instructions}

### Order format
Write your adjustment orders as a JSON object and encrypt with the \
GM's public key:

```bash
cat <<'ORDERS' | gpg --armor --encrypt --trust-model always \\
    --recipient gm@perfid.local \\
    --output orders/{year}/Winter_Adjustment/{power}.gpg
{{
  "power": "{power}",
  "year": {year},
  "phase": "Winter Adjustment",
  "orders": [
{example_orders}
  ]
}}
ORDERS
```
"""


def adjustment_prompt(power, state, game_dir):
    """Generate the winter adjustment prompt.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.

    Returns:
        Adjustment prompt string.
    """
    pview = state_for_power(state, power)
    adj = adjustment_counts(state)
    diff = adj.get(power, 0)
    units = pview["your_units"]
    unit_count = len(units)

    # Determine adjustment type
    if diff > 0:
        adjustment_desc = f"**Build {diff} unit{'s' if diff != 1 else ''}**"
        adjustment_instructions = _build_instructions(power, state, diff)
    elif diff < 0:
        adjustment_desc = (
            f"**Disband {abs(diff)} unit{'s' if abs(diff) != 1 else ''}**"
        )
        adjustment_instructions = _disband_instructions(power, units, diff)
    else:
        adjustment_desc = "**No adjustment needed** (units == SCs)"
        adjustment_instructions = (
            "You have exactly as many units as supply centers. "
            "No builds or disbands are required. You may submit an "
            "empty orders list or simply skip this phase."
        )

    # Format home center availability for builds
    home_centers = HOME_CENTERS[power]
    occupied_locs = {
        u["location"].split("(")[0].strip()
        for p in POWERS
        for u in state["units"].get(p, [])
    }
    home_status_lines = []
    for hc in home_centers:
        owned = state["sc_ownership"].get(hc) == power
        occupied = hc in occupied_locs
        if owned and not occupied:
            home_status_lines.append(f"  {hc} — available for build")
        elif owned and occupied:
            home_status_lines.append(f"  {hc} — occupied (cannot build)")
        else:
            home_status_lines.append(f"  {hc} — not owned (cannot build)")
    home_center_status = "\n".join(home_status_lines)

    # Format owned SCs
    owned_scs = sorted(
        sc for sc, owner in state["sc_ownership"].items()
        if owner == power
    )
    your_scs = "  " + ", ".join(owned_scs) if owned_scs else "  (none)"

    # Example orders
    if diff > 0:
        available = [
            hc for hc in home_centers
            if state["sc_ownership"].get(hc) == power and hc not in occupied_locs
        ]
        examples = []
        for hc in available[:diff]:
            examples.append(f'    "Build A {hc}"')
        if not examples:
            examples.append('    "Waive"')
        example_orders = ",\n".join(examples)
    elif diff < 0:
        examples = []
        for u in units[:abs(diff)]:
            ut = u["type"][0]
            examples.append(f'    "Disband {ut} {u["location"]}"')
        example_orders = ",\n".join(examples) if examples else '    "Waive"'
    else:
        example_orders = '    "Waive"'

    return ADJUSTMENT_PROMPT.format(
        power=power,
        year=state["year"],
        your_sc_count=pview["your_sc_count"],
        unit_count=unit_count,
        adjustment_desc=adjustment_desc,
        your_units=_format_units(units),
        your_scs=your_scs,
        home_center_status=home_center_status,
        sc_ownership=_format_sc_ownership(state),
        adjustment_instructions=adjustment_instructions,
        example_orders=example_orders,
    )


def _build_instructions(power, state, builds):
    """Generate build-specific instructions."""
    home_centers = HOME_CENTERS[power]
    occupied_locs = {
        u["location"].split("(")[0].strip()
        for p in POWERS
        for u in state["units"].get(p, [])
    }
    available = [
        hc for hc in home_centers
        if state["sc_ownership"].get(hc) == power and hc not in occupied_locs
    ]
    n_available = len(available)

    lines = [
        f"You may build up to **{builds}** new unit(s).",
        "Choose wisely — every build should support your push toward 18 SCs.",
        "",
        "Build rules:",
        "- Builds can only be placed in your **home supply centers**",
        "- The home center must be **owned by you** and **unoccupied**",
        f"- Available home centers for builds: "
        f"{', '.join(available) if available else '(none)'}",
    ]

    if n_available < builds:
        lines.append(
            f"- Note: you can only build {n_available} unit(s) "
            f"(not enough open home centers for all {builds})"
        )
        lines.append("- Use `Waive` to decline remaining builds")

    lines.extend([
        "",
        "For each build, specify the unit type (Army or Fleet):",
        "- `Build A <location>` for an Army",
        "- `Build F <location>` for a Fleet",
        "- `Waive` to decline a build",
    ])

    return "\n".join(lines)


def _disband_instructions(power, units, diff):
    """Generate disband-specific instructions."""
    n_disband = abs(diff)
    lines = [
        f"You must disband **{n_disband}** unit(s) "
        f"(you have more units than supply centers).",
        "",
        "Choose which units to remove:",
    ]
    for u in units:
        ut = u["type"][0]
        lines.append(f"  - `Disband {ut} {u['location']}`")

    lines.extend([
        "",
        f"Submit exactly {n_disband} disband order(s). If you fail "
        f"to submit valid orders, units will be disbanded automatically "
        f"(furthest from home centers first).",
    ])

    return "\n".join(lines)


def turn_context(power, state, game_dir, round_num=None,
                 max_rounds=None):
    """Generate the appropriate per-turn prompt based on current phase.

    Dispatches to the correct prompt generator based on the game phase.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.
        round_num: Negotiation round (required for diplomacy phases).
        max_rounds: Total negotiation rounds (required for diplomacy phases).

    Returns:
        Turn-specific prompt string.
    """
    phase = Phase(state["phase"])

    if phase in (Phase.SPRING_DIPLOMACY, Phase.FALL_DIPLOMACY):
        if round_num is None or max_rounds is None:
            raise ValueError(
                "round_num and max_rounds required for diplomacy phases"
            )
        return negotiation_prompt(
            power, state, game_dir, round_num, max_rounds
        )

    if phase in (Phase.SPRING_MOVEMENT, Phase.FALL_MOVEMENT):
        return order_prompt(power, state, game_dir)

    if phase in (Phase.SPRING_RETREAT, Phase.FALL_RETREAT):
        return retreat_prompt(power, state, game_dir)

    if phase == Phase.WINTER_ADJUSTMENT:
        return adjustment_prompt(power, state, game_dir)

    raise ValueError(f"Unknown phase: {phase}")
