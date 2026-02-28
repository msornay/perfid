"""Diplomacy game state management.

Handles game state representation, phase progression, supply center
tracking, win condition detection, and save/load. Delegates
adjudication to jDip for DATC-compliant game logic.
"""

import json
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class Phase(Enum):
    SPRING_DIPLOMACY = "Spring Diplomacy"
    SPRING_MOVEMENT = "Spring Movement"
    SPRING_RETREAT = "Spring Retreat"
    FALL_DIPLOMACY = "Fall Diplomacy"
    FALL_MOVEMENT = "Fall Movement"
    FALL_RETREAT = "Fall Retreat"
    WINTER_ADJUSTMENT = "Winter Adjustment"


PHASE_ORDER = [
    Phase.SPRING_DIPLOMACY,
    Phase.SPRING_MOVEMENT,
    Phase.SPRING_RETREAT,
    Phase.FALL_DIPLOMACY,
    Phase.FALL_MOVEMENT,
    Phase.FALL_RETREAT,
    Phase.WINTER_ADJUSTMENT,
]

POWERS = [
    "Austria",
    "England",
    "France",
    "Germany",
    "Italy",
    "Russia",
    "Turkey",
]

# Standard Diplomacy starting positions (1901 Spring)
STARTING_UNITS = {
    "Austria": [
        {"type": "Army", "location": "Vienna"},
        {"type": "Army", "location": "Budapest"},
        {"type": "Fleet", "location": "Trieste"},
    ],
    "England": [
        {"type": "Fleet", "location": "London"},
        {"type": "Fleet", "location": "Edinburgh"},
        {"type": "Army", "location": "Liverpool"},
    ],
    "France": [
        {"type": "Fleet", "location": "Brest"},
        {"type": "Army", "location": "Paris"},
        {"type": "Army", "location": "Marseilles"},
    ],
    "Germany": [
        {"type": "Fleet", "location": "Kiel"},
        {"type": "Army", "location": "Berlin"},
        {"type": "Army", "location": "Munich"},
    ],
    "Italy": [
        {"type": "Fleet", "location": "Naples"},
        {"type": "Army", "location": "Rome"},
        {"type": "Army", "location": "Venice"},
    ],
    "Russia": [
        {"type": "Fleet", "location": "St Petersburg (South Coast)"},
        {"type": "Army", "location": "Moscow"},
        {"type": "Army", "location": "Warsaw"},
        {"type": "Fleet", "location": "Sevastopol"},
    ],
    "Turkey": [
        {"type": "Fleet", "location": "Ankara"},
        {"type": "Army", "location": "Constantinople"},
        {"type": "Army", "location": "Smyrna"},
    ],
}

# Home supply centers per power
HOME_CENTERS = {
    "Austria": ["Vienna", "Budapest", "Trieste"],
    "England": ["London", "Edinburgh", "Liverpool"],
    "France": ["Brest", "Paris", "Marseilles"],
    "Germany": ["Kiel", "Berlin", "Munich"],
    "Italy": ["Naples", "Rome", "Venice"],
    "Russia": [
        "St Petersburg",
        "Moscow",
        "Warsaw",
        "Sevastopol",
    ],
    "Turkey": ["Ankara", "Constantinople", "Smyrna"],
}

# All 34 supply centers on the standard map
ALL_SUPPLY_CENTERS = sorted(
    {sc for centers in HOME_CENTERS.values() for sc in centers}
    | {
        "Belgium",
        "Bulgaria",
        "Denmark",
        "Greece",
        "Holland",
        "Norway",
        "Portugal",
        "Rumania",
        "Serbia",
        "Spain",
        "Sweden",
        "Tunis",
    }
)

WIN_THRESHOLD = 18  # SCs needed to win


def new_game(game_id, game_dir, use_jdip=True):
    """Create a new game with standard starting positions.

    When use_jdip is True (default), starting positions and supply
    center ownership come from jDip — the DATC-compliant source of
    truth. Falls back to hardcoded positions if jDip is unavailable.

    Args:
        game_id: Unique identifier for this game.
        game_dir: Path to the game directory (perfid-games/<game-id>/).
        use_jdip: If True, get starting state from jDip.

    Returns:
        The initial game state dict.
    """
    game_dir = Path(game_dir)
    game_dir.mkdir(parents=True, exist_ok=True)

    units = None
    sc_ownership = None

    if use_jdip:
        try:
            import jdip_adapter
            if jdip_adapter.is_available():
                jdip_state = jdip_adapter.jdip_init()
                # Normalize jDip location names to perfid format
                units = jdip_adapter._units_from_jdip(
                    jdip_state["units"]
                )
                sc_ownership = jdip_adapter._sc_from_jdip(
                    jdip_state["sc_ownership"]
                )
        except (ImportError, RuntimeError):
            pass  # Fall back to hardcoded positions

    if units is None:
        units = deepcopy(STARTING_UNITS)
    if sc_ownership is None:
        sc_ownership = {}
        for power, centers in HOME_CENTERS.items():
            for sc in centers:
                sc_ownership[sc] = power

    state = {
        "game_id": game_id,
        "year": 1901,
        "phase": Phase.SPRING_DIPLOMACY.value,
        "units": units,
        "sc_ownership": sc_ownership,
        "eliminated": [],
        "winner": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    save_state(state, game_dir)
    return state


def save_state(state, game_dir):
    """Save game state to state.json in the game directory."""
    game_dir = Path(game_dir)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state_path = game_dir / "state.json"
    state_path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n"
    )


def load_state(game_dir):
    """Load game state from state.json in the game directory."""
    game_dir = Path(game_dir)
    state_path = game_dir / "state.json"
    return json.loads(state_path.read_text())


def get_phase(state):
    """Return the current Phase enum."""
    return Phase(state["phase"])


def next_phase(state):
    """Advance to the next phase. Increments year after Winter.

    Returns the updated state (mutated in place).
    """
    current = Phase(state["phase"])
    idx = PHASE_ORDER.index(current)

    if idx == len(PHASE_ORDER) - 1:
        # After Winter Adjustment, advance year and go to Spring
        state["year"] += 1
        state["phase"] = PHASE_ORDER[0].value
    else:
        state["phase"] = PHASE_ORDER[idx + 1].value

    return state


def skip_retreat_if_empty(state):
    """Skip retreat phase if no retreats are needed.

    In standard Diplomacy, if no units were dislodged during
    movement, the retreat phase is skipped entirely.

    Args:
        state: Game state dict. Must include a 'dislodged' key
               (list of dislodged units) if retreats are pending.

    Returns:
        Updated state, potentially advanced past the retreat phase.
    """
    phase = Phase(state["phase"])
    if phase in (Phase.SPRING_RETREAT, Phase.FALL_RETREAT):
        dislodged = state.get("dislodged", [])
        if not dislodged:
            state = next_phase(state)
    return state


def sc_counts(state):
    """Return a dict of power → number of supply centers owned."""
    counts = {p: 0 for p in POWERS}
    for sc, owner in state["sc_ownership"].items():
        if owner in counts:
            counts[owner] += 1
    return counts


def check_win(state):
    """Check if any power has won (18+ supply centers).

    Returns the winning power name, or None.
    """
    for power, count in sc_counts(state).items():
        if count >= WIN_THRESHOLD:
            return power
    return None


def update_sc_ownership(state):
    """Update supply center ownership after Fall Retreat.

    A power gains ownership of a supply center if one of its units
    occupies that SC at the end of a Fall turn (after retreats).

    Returns the updated state.
    """
    for power, units in state["units"].items():
        for unit in units:
            loc = unit["location"]
            # Normalize coast variants for SC matching
            base_loc = loc.split("(")[0].strip()
            if base_loc in state["sc_ownership"]:
                state["sc_ownership"][base_loc] = power
    return state


def check_elimination(state):
    """Mark powers with 0 supply centers as eliminated.

    Returns the updated state.
    """
    counts = sc_counts(state)
    for power in POWERS:
        if counts[power] == 0 and power not in state["eliminated"]:
            state["eliminated"].append(power)
    return state


def adjustment_counts(state):
    """Calculate build/disband counts for Winter Adjustment.

    Returns a dict of power → int, where positive means builds
    available and negative means units must be disbanded.
    """
    counts = sc_counts(state)
    result = {}
    for power in POWERS:
        if power in state["eliminated"]:
            result[power] = 0
            continue
        n_units = len(state["units"].get(power, []))
        n_scs = counts[power]
        result[power] = n_scs - n_units
    return result


def apply_orders(state, orders, game_dir=None):
    """Apply adjudicated orders to game state.

    Accepts a results dict, either from jDip (via jdip_adjudicate)
    or from a manual adjudicator.

    Args:
        state: Current game state.
        orders: Dict with adjudication results:
            {
                "resolved_units": {power: [unit_dicts]},
                "dislodged": [{"power": str, "unit": unit_dict,
                               "retreats": [locations]}],
                "sc_ownership": {sc: owner} (optional, from jDip),
            }
        game_dir: Optional game directory for saving state.

    Returns:
        Updated state.
    """
    if "resolved_units" in orders:
        state["units"] = orders["resolved_units"]
    if "dislodged" in orders:
        state["dislodged"] = orders["dislodged"]
    else:
        state["dislodged"] = []
    if "sc_ownership" in orders and orders["sc_ownership"]:
        state["sc_ownership"] = orders["sc_ownership"]

    phase = Phase(state["phase"])

    # After Fall Retreat, update SC ownership and check win
    if phase == Phase.FALL_RETREAT:
        state = update_sc_ownership(state)
        state = check_elimination(state)
        winner = check_win(state)
        if winner:
            state["winner"] = winner

    if game_dir:
        save_state(state, game_dir)

    return state


def adjudicate(state, all_orders, game_dir=None):
    """Adjudicate orders via jDip and apply results to state.

    This is the primary entry point for DATC-compliant adjudication.
    Uses jDip's SC ownership and game_over flag for win detection.

    Args:
        state: Current game state dict.
        all_orders: Dict of power -> list of order strings.
            e.g. {"France": ["A Paris - Burgundy", "F Brest H"]}
        game_dir: Optional game directory for saving state.

    Returns:
        Updated state with adjudication applied and phase advanced.
    """
    import jdip_adapter

    phase_label = state["phase"]
    year = state["year"]

    result = jdip_adapter.jdip_adjudicate(
        phase_label, year,
        state["units"],
        state["sc_ownership"],
        all_orders,
    )

    state = apply_orders(state, result, game_dir)

    # Check win using SC counts from jDip's updated ownership
    if result.get("game_over"):
        winner = check_win(state)
        if winner:
            state["winner"] = winner
    state = check_elimination(state)

    state = next_phase(state)
    state = skip_retreat_if_empty(state)

    if game_dir:
        save_state(state, game_dir)

    return state


def apply_retreats(state, retreat_orders, game_dir=None):
    """Apply retreat orders.

    Args:
        state: Current game state.
        retreat_orders: List of dicts:
            [{"power": str, "unit": unit_dict,
              "action": "retreat", "destination": str}]
            or
            [{"power": str, "unit": unit_dict,
              "action": "disband"}]
        game_dir: Optional game directory for saving state.

    Returns:
        Updated state.
    """
    for order in retreat_orders:
        power = order["power"]
        if order["action"] == "retreat":
            state["units"][power].append(
                {
                    "type": order["unit"]["type"],
                    "location": order["destination"],
                }
            )
        # "disband" means the unit is simply removed (already not
        # in the units list since it was dislodged)

    state["dislodged"] = []

    # After Fall Retreat, update SC ownership
    phase = Phase(state["phase"])
    if phase == Phase.FALL_RETREAT:
        state = update_sc_ownership(state)
        state = check_elimination(state)
        winner = check_win(state)
        if winner:
            state["winner"] = winner

    if game_dir:
        save_state(state, game_dir)

    return state


def apply_adjustments(state, adjustments, game_dir=None):
    """Apply Winter Adjustment builds/disbands.

    Args:
        state: Current game state.
        adjustments: Dict of power → list of actions:
            {"France": [
                {"action": "build", "unit": {"type": "Army",
                                              "location": "Paris"}},
                {"action": "disband", "unit": {"type": "Fleet",
                                                "location": "Brest"}},
            ]}
        game_dir: Optional game directory for saving state.

    Returns:
        Updated state.
    """
    for power, actions in adjustments.items():
        for action in actions:
            if action["action"] == "build":
                state["units"].setdefault(power, []).append(
                    action["unit"]
                )
            elif action["action"] == "disband":
                unit = action["unit"]
                state["units"][power] = [
                    u
                    for u in state["units"][power]
                    if not (
                        u["type"] == unit["type"]
                        and u["location"] == unit["location"]
                    )
                ]

    if game_dir:
        save_state(state, game_dir)

    return state


def state_for_power(state, power):
    """Return a view of the game state for a specific power.

    All information is public in Diplomacy (units, SCs), but this
    structures it from the perspective of a single power for prompt
    generation.
    """
    counts = sc_counts(state)
    return {
        "game_id": state["game_id"],
        "year": state["year"],
        "phase": state["phase"],
        "power": power,
        "your_units": state["units"].get(power, []),
        "your_sc_count": counts.get(power, 0),
        "your_home_centers": HOME_CENTERS.get(power, []),
        "all_units": state["units"],
        "sc_ownership": state["sc_ownership"],
        "sc_counts": counts,
        "eliminated": state["eliminated"],
        "winner": state["winner"],
    }


def format_status(state):
    """Return a human-readable status string."""
    lines = [
        f"Game: {state['game_id']}",
        f"Year: {state['year']} — {state['phase']}",
        "",
        "Supply Center Counts:",
    ]
    counts = sc_counts(state)
    for power in POWERS:
        n = counts[power]
        elim = " (eliminated)" if power in state["eliminated"] else ""
        lines.append(f"  {power:12s} {n:2d} SCs{elim}")

    if state["winner"]:
        lines.append(f"\nWinner: {state['winner']}")

    lines.append(f"\nTotal SCs owned: {sum(counts.values())}/34")
    return "\n".join(lines)
