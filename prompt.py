"""Agent prompt generation for perfid Diplomacy games.

Builds system prompts and per-turn context for each agent. The prompts
instruct the Claude agent (running in a Docker container) on how to play
Diplomacy, use GPG encryption, and interact with the game filesystem.

Prompt types:
  - system_prompt: one-time rules, GPG workflow, file layout
  - season_turn_prompt: free-form turn (negotiate + submit orders)
  - retreat_prompt: choose retreat destinations or disband
  - adjustment_prompt: choose builds/disbands for winter
"""


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
            f"  From {msg['sender']}: {msg['path']}"
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
        retreat_str = (
            ", ".join(retreats)
            if retreats
            else "(none — must disband)"
        )
        lines.append(
            f"  {u['type']} {u['location']} — "
            f"can retreat to: {retreat_str}"
        )
    return "\n".join(lines)


SYSTEM_PROMPT = """\
You are a Diplomacy AI agent playing as {power} in a 7-player standard \
Diplomacy game. You are one of 7 independent AI agents, each running in \
a shared Docker container with isolated GPG keyrings.

# Diplomacy Rules Summary

Diplomacy is a strategy game set in pre-WWI Europe. 7 Great Powers \
(Austria, England, France, Germany, Italy, Russia, Turkey) compete for \
control of supply centers (SCs). The first power to control 18 of the \
34 SCs wins a solo victory.

## Phases (each game year)
1. **Spring** — negotiate with other powers and submit movement orders
2. **Spring Retreat** — retreat or disband dislodged units
3. **Fall** — negotiate with other powers and submit movement orders
4. **Fall Retreat** — retreat or disband
5. **Winter Adjustment** — build new units or disband excess

## Free-form Turns (Spring & Fall)

On your turn you can send messages to negotiate, use jDip to simulate \
strategies, or submit your final movement orders. Once you submit \
orders your turn is done for the season. You will be called multiple \
times during a season — each call is a chance to read new messages, \
send replies, and decide whether to submit orders now or wait.

## Unit Types
- **Army (A)** — moves on land
- **Fleet (F)** — moves on sea and coastal provinces

## Order Types (Spring & Fall)
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
is injected at the start of each turn and removed afterwards.

**CRITICAL: NEVER write plaintext to disk.** All messages, orders, and \
notes MUST be GPG-encrypted. Other agents share the filesystem and can \
read any unencrypted file. If gpg encryption fails, do NOT fall back to \
writing plaintext — diagnose and fix the gpg command instead.

## Sending messages
1. Compose your message as plaintext
2. Encrypt with the recipient's public key:
   ```
   gpg --armor --encrypt --trust-model always \\
       --recipient <recipient>@perfid.local \\
       --output messages/outbox/{power}/<filename>.gpg
   ```
   Use filename format: `{power}-to-<recipient>-<phase>-r1-<seq>.gpg`

## Reading messages
1. List your inbox: `ls messages/inbox/{power}/`
2. Decrypt each message:
   ```
   gpg --decrypt messages/inbox/{power}/<filename>.gpg
   ```

## Submitting orders
**Do NOT submit orders until the turn prompt explicitly says you may.** \
Early rounds are negotiation-only. When order submission is allowed, the \
turn prompt will provide the exact command and destination path. Never \
write to the orders directory on your own.

## Private notes
Encrypt notes with your own key to persist thoughts across turns. \
NEVER write unencrypted notes — other agents can read the filesystem.
```
gpg --armor --encrypt --trust-model always \\
    --recipient {power_lower}@perfid.local \\
    --output notes/{power}/<year>-<phase>.gpg
```

# Strategy Simulation (jDip)

You have access to jDip, a DATC-compliant Diplomacy adjudicator, to test \
order combinations **before** committing. Use it to evaluate strategies:

```python
import json, sys
sys.path.insert(0, '/perfid')
from jdip_adapter import simulate

state = json.load(open("state.json"))
result = simulate(state, {{
    "{power}": ["A Paris - Burgundy", "F Brest - English Channel"],
    "Germany": ["A Munich - Burgundy"],  # guess opponent orders
}})

# Check results
for r in result["order_results"]:
    print(r["order"], r["result"])

# Per-power summary
print(result["summary"]["{power}"])
```

The `simulate()` function runs adjudication **without modifying the game \
state**, so you can safely test many combinations. Use it to:
- Verify your orders will succeed against likely opponent moves
- Test support configurations
- Evaluate whether a stab will work before committing

# File Layout
```
/perfid/              — game engine (read-only mount)
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


NEGOTIATION_PROMPT = """\
# {phase} {year} — Negotiation Round {round_num}/{min_negotiation_rounds}

**Year**: {year} | **Phase**: {phase} | **Round**: {round_num} of \
{min_negotiation_rounds} (negotiation only)

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

## Your Inbox
{inbox}

## Instructions

This is **negotiation round {round_num}/{min_negotiation_rounds}**. \
You cannot submit orders yet. Use this round to:

1. **Read** any messages in your inbox by decrypting them:
   ```
   gpg --decrypt messages/inbox/{power}/<filename>.gpg
   ```

2. **Send messages** to negotiate with other powers:
   ```
   echo "Your message here" | gpg --armor --encrypt --trust-model always \\
       --recipient <recipient>@perfid.local \\
       --output messages/outbox/{power}/{power}-to-<Recipient>-{phase_label}-r{round_num}-<seq>.gpg
   ```

3. **Use jDip** to simulate order combinations and test strategies.

4. **Plan your strategy.** Think about alliances, threats, and openings.

**Your goal is 18 SCs — solo victory.** Think carefully about:
- Who to ally with and what to propose
- What are your opponents likely planning?
- Which supply centers can you take this year?
- Who is the biggest threat to reach 18 first?
"""

SEASON_TURN_PROMPT = """\
# {phase} {year} — Round {round_num}

**Year**: {year} | **Phase**: {phase} | **Round**: {round_num}

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

## Your Inbox
{inbox}

## Instructions

You may negotiate, simulate strategies, and **submit your final orders**.

1. **Read** any messages in your inbox by decrypting them:
   ```
   gpg --decrypt messages/inbox/{power}/<filename>.gpg
   ```

2. **Send messages** to negotiate with other powers:
   ```
   echo "Your message here" | gpg --armor --encrypt --trust-model always \\
       --recipient <recipient>@perfid.local \\
       --output messages/outbox/{power}/{power}-to-<Recipient>-{phase_label}-r{round_num}-<seq>.gpg
   ```

3. **Use jDip** to simulate order combinations and test strategies.

4. **Submit your final orders** when ready:

### Order format
Write your orders as a JSON object and encrypt with the GM's public key:

```bash
cat <<'ORDERS' | gpg --armor --encrypt --trust-model always \\
    --recipient gm@perfid.local \\
    --output {dropbox}/{power}.gpg
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


def season_turn_prompt(power, state, game_dir, round_num=1,
                       dropbox=None, min_negotiation_rounds=2):
    """Generate the season turn prompt.

    In negotiation rounds (round_num <= min_negotiation_rounds),
    generates a negotiation-only prompt. In later rounds, includes
    order submission instructions pointing to the dropbox.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.
        round_num: Current round number (1-indexed).
        dropbox: Path to the power's order dropbox directory.
            Required when round_num > min_negotiation_rounds.
        min_negotiation_rounds: Negotiation rounds before orders
            are allowed.

    Returns:
        Season turn prompt string.
    """
    phase = state["phase"]
    phase_label = phase.replace(" ", "_")
    pview = state_for_power(state, power)

    inbox = list_inbox(game_dir, power)

    common = dict(
        power=power,
        year=state["year"],
        phase=phase,
        phase_label=phase_label,
        round_num=round_num,
        min_negotiation_rounds=min_negotiation_rounds,
        all_units=_format_all_units(state),
        sc_ownership=_format_sc_ownership(state),
        your_units=_format_units(pview["your_units"]),
        your_sc_count=pview["your_sc_count"],
        inbox=_format_inbox(inbox),
    )

    if round_num <= min_negotiation_rounds:
        return NEGOTIATION_PROMPT.format(**common)

    # Build example orders from the power's actual units
    example_lines = []
    for u in pview["your_units"]:
        ut = u["type"][0]  # "A" or "F"
        example_lines.append(f'    "{ut} {u["location"]} H"')
    example_orders = (
        ",\n".join(example_lines)
        if example_lines
        else '    "Waive"'
    )

    dropbox_path = dropbox or f"/tmp/orders-{power.lower()}"

    return SEASON_TURN_PROMPT.format(
        **common,
        dropbox=dropbox_path,
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
    --output {dropbox}/{power}.gpg
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


def retreat_prompt(power, state, game_dir, dropbox=None):
    """Generate the retreat prompt for dislodged units.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.
        dropbox: Path to the power's order dropbox directory.

    Returns:
        Retreat prompt string.
    """
    phase = state["phase"]
    phase_label = phase.replace(" ", "_")
    dislodged = state.get("dislodged", [])
    dropbox_path = dropbox or f"/tmp/orders-{power.lower()}"

    return RETREAT_PROMPT.format(
        power=power,
        year=state["year"],
        phase=phase,
        phase_label=phase_label,
        all_units=_format_all_units(state),
        dislodged=_format_dislodged(dislodged, power),
        dropbox=dropbox_path,
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
    --output {dropbox}/{power}.gpg
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


def adjustment_prompt(power, state, game_dir, dropbox=None):
    """Generate the winter adjustment prompt.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.
        dropbox: Path to the power's order dropbox directory.

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
        adjustment_desc = (
            f"**Build {diff} unit{'s' if diff != 1 else ''}**"
        )
        adjustment_instructions = _build_instructions(
            power, state, diff
        )
    elif diff < 0:
        adjustment_desc = (
            f"**Disband {abs(diff)} "
            f"unit{'s' if abs(diff) != 1 else ''}**"
        )
        adjustment_instructions = _disband_instructions(
            power, units, diff
        )
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
            home_status_lines.append(
                f"  {hc} — available for build"
            )
        elif owned and occupied:
            home_status_lines.append(
                f"  {hc} — occupied (cannot build)"
            )
        else:
            home_status_lines.append(
                f"  {hc} — not owned (cannot build)"
            )
    home_center_status = "\n".join(home_status_lines)

    # Format owned SCs
    owned_scs = sorted(
        sc for sc, owner in state["sc_ownership"].items()
        if owner == power
    )
    your_scs = (
        "  " + ", ".join(owned_scs) if owned_scs else "  (none)"
    )

    # Example orders
    if diff > 0:
        available = [
            hc for hc in home_centers
            if (
                state["sc_ownership"].get(hc) == power
                and hc not in occupied_locs
            )
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
            examples.append(
                f'    "Disband {ut} {u["location"]}"'
            )
        example_orders = (
            ",\n".join(examples) if examples else '    "Waive"'
        )
    else:
        example_orders = '    "Waive"'

    dropbox_path = dropbox or f"/tmp/orders-{power.lower()}"

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
        dropbox=dropbox_path,
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
        if (
            state["sc_ownership"].get(hc) == power
            and hc not in occupied_locs
        )
    ]
    n_available = len(available)

    lines = [
        f"You may build up to **{builds}** new unit(s).",
        "Choose wisely — every build should support your push "
        "toward 18 SCs.",
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
                 dropbox=None, min_negotiation_rounds=2):
    """Generate the appropriate per-turn prompt based on phase.

    Dispatches to the correct prompt generator based on the game
    phase.

    Args:
        power: Diplomacy power name.
        state: Current game state dict.
        game_dir: Path to game directory.
        round_num: For movement phases, the current round number.
        dropbox: For movement phases, the order dropbox path.
        min_negotiation_rounds: Negotiation rounds before orders
            are allowed.

    Returns:
        Turn-specific prompt string.
    """
    phase = Phase(state["phase"])

    if phase in (Phase.SPRING, Phase.FALL):
        return season_turn_prompt(
            power, state, game_dir,
            round_num=round_num or (min_negotiation_rounds + 1),
            dropbox=dropbox,
            min_negotiation_rounds=min_negotiation_rounds,
        )

    if phase in (Phase.SPRING_RETREAT, Phase.FALL_RETREAT):
        return retreat_prompt(power, state, game_dir, dropbox=dropbox)

    if phase == Phase.WINTER_ADJUSTMENT:
        return adjustment_prompt(power, state, game_dir, dropbox=dropbox)

    raise ValueError(f"Unknown phase: {phase}")
