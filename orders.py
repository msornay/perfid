"""Agent I/O: order submission, decryption, validation, and private notes.

Handles the encrypted order pipeline between agents and the GM:
  - Agents submit orders encrypted with the GM's public key
  - GM decrypts all orders for adjudication
  - Basic validation (syntax, unit ownership, phase-appropriate types)
  - Agents write private notes encrypted with their own key

Directory layout inside a game dir:
    orders/
        <year>/
            <phase>/
                <power>.gpg       — encrypted order (only GM can read)
                <power>.json      — decrypted order (written by GM)
    notes/
        <power>/
            <year>-<phase>.gpg    — private notes (only the agent can read)
"""

import json
import re
from pathlib import Path

import gpg as gpg_mod
from game_state import PHASE_ORDER, POWERS, Phase


# --- Diplomacy order parsing ---

# All valid provinces on the standard map
PROVINCES = {
    # Land provinces
    "Bohemia", "Budapest", "Galicia", "Trieste", "Tyrolia", "Vienna",
    "Clyde", "Edinburgh", "Liverpool", "London", "Wales", "Yorkshire",
    "Brest", "Burgundy", "Gascony", "Marseilles", "Paris", "Picardy",
    "Berlin", "Kiel", "Munich", "Prussia", "Ruhr", "Silesia",
    "Apulia", "Naples", "Piedmont", "Rome", "Tuscany", "Venice",
    "Finland", "Livonia", "Moscow", "Sevastopol", "St Petersburg",
    "Ukraine", "Warsaw",
    "Ankara", "Armenia", "Constantinople", "Smyrna", "Syria",
    # Neutral land
    "Albania", "Belgium", "Bulgaria", "Denmark", "Greece", "Holland",
    "Norway", "Portugal", "Rumania", "Serbia", "Spain", "Sweden",
    "Tunis", "North Africa",
    # Sea provinces
    "Adriatic Sea", "Aegean Sea", "Baltic Sea", "Barents Sea",
    "Black Sea", "Eastern Mediterranean", "English Channel",
    "Gulf of Bothnia", "Gulf of Lyon", "Helgoland Bight",
    "Ionian Sea", "Irish Sea", "Mid-Atlantic Ocean",
    "North Atlantic Ocean", "North Sea", "Norwegian Sea",
    "Skagerrak", "Tyrrhenian Sea", "Western Mediterranean",
}

# Coasts for provinces with multiple coasts
COASTS = {
    "St Petersburg": ["North Coast", "South Coast"],
    "Spain": ["North Coast", "South Coast"],
    "Bulgaria": ["North Coast", "South Coast"],
}

UNIT_TYPES = {"Army", "Fleet", "A", "F"}

# Order type patterns (case-insensitive matching done at parse time)
# Movement: A Paris - Burgundy / F London - North Sea
# Hold: A Paris H / A Paris Hold
# Support: A Munich S A Berlin - Silesia / A Munich S A Paris H
# Convoy: F North Sea C A London - Belgium
# Retreat: A Munich - Bohemia (same as move syntax, used in retreat phase)
# Disband (retreat): A Munich Disband
# Build: Build A Paris / Build F London
# Disband (winter): Disband A Paris / Remove A Paris

# Regex for location with optional coast.
# Locations may contain hyphens (e.g. "Mid-Atlantic Ocean").
# Move separators require surrounding spaces to disambiguate.
_LOC = r"([A-Za-z][A-Za-z .\-]+?)(?:\s*\(([^)]+)\))?"

# Order regexes — move separators use \s+[-–—]\s+ to require spaces
_MOVE_RE = re.compile(
    rf"^([AF])\s+{_LOC}\s+[-–—]\s+{_LOC}$", re.IGNORECASE
)
_HOLD_RE = re.compile(
    rf"^([AF])\s+{_LOC}\s+(?:H|Hold)$", re.IGNORECASE
)
_SUPPORT_MOVE_RE = re.compile(
    rf"^([AF])\s+{_LOC}\s+(?:S|Support)\s+([AF])\s+{_LOC}\s+[-–—]\s+{_LOC}$",
    re.IGNORECASE,
)
_SUPPORT_HOLD_RE = re.compile(
    rf"^([AF])\s+{_LOC}\s+(?:S|Support)\s+([AF])\s+{_LOC}\s*(?:H|Hold)?$",
    re.IGNORECASE,
)
_CONVOY_RE = re.compile(
    rf"^([AF])\s+{_LOC}\s+(?:C|Convoy)\s+([AF])\s+{_LOC}\s+[-–—]\s+{_LOC}$",
    re.IGNORECASE,
)
_RETREAT_DISBAND_RE = re.compile(
    rf"^([AF])\s+{_LOC}\s+(?:Disband|D)$", re.IGNORECASE
)
_BUILD_RE = re.compile(
    rf"^Build\s+([AF])\s+{_LOC}$", re.IGNORECASE
)
_WINTER_DISBAND_RE = re.compile(
    rf"^(?:Disband|Remove)\s+([AF])\s+{_LOC}$", re.IGNORECASE
)
_WAIVE_RE = re.compile(
    r"^Waive$", re.IGNORECASE
)


def parse_order(order_str):
    """Parse a Diplomacy order string into a structured dict.

    Returns a dict with keys depending on order type:
        - type: "move", "hold", "support_move", "support_hold",
                "convoy", "retreat_disband", "build", "disband", "waive"
        - unit_type: "A" or "F"
        - location: province name (stripped)
        - coast: coast string or None
        - (type-specific fields like destination, supported_unit, etc.)

    Returns None if the order cannot be parsed.
    """
    order_str = order_str.strip()

    # Waive (winter — decline a build)
    if _WAIVE_RE.match(order_str):
        return {"type": "waive"}

    # Build
    m = _BUILD_RE.match(order_str)
    if m:
        return {
            "type": "build",
            "unit_type": m.group(1).upper(),
            "location": m.group(2).strip(),
            "coast": m.group(3),
        }

    # Winter disband
    m = _WINTER_DISBAND_RE.match(order_str)
    if m:
        return {
            "type": "disband",
            "unit_type": m.group(1).upper(),
            "location": m.group(2).strip(),
            "coast": m.group(3),
        }

    # Retreat disband
    m = _RETREAT_DISBAND_RE.match(order_str)
    if m:
        return {
            "type": "retreat_disband",
            "unit_type": m.group(1).upper(),
            "location": m.group(2).strip(),
            "coast": m.group(3),
        }

    # Support move
    m = _SUPPORT_MOVE_RE.match(order_str)
    if m:
        return {
            "type": "support_move",
            "unit_type": m.group(1).upper(),
            "location": m.group(2).strip(),
            "coast": m.group(3),
            "supported_type": m.group(4).upper(),
            "supported_loc": m.group(5).strip(),
            "supported_coast": m.group(6),
            "destination": m.group(7).strip(),
            "dest_coast": m.group(8),
        }

    # Convoy
    m = _CONVOY_RE.match(order_str)
    if m:
        return {
            "type": "convoy",
            "unit_type": m.group(1).upper(),
            "location": m.group(2).strip(),
            "coast": m.group(3),
            "convoyed_type": m.group(4).upper(),
            "convoyed_loc": m.group(5).strip(),
            "convoyed_coast": m.group(6),
            "destination": m.group(7).strip(),
            "dest_coast": m.group(8),
        }

    # Support hold (after support_move, but before move/hold to avoid
    # the hold regex consuming "A Burgundy S A Paris H" as a hold)
    m = _SUPPORT_HOLD_RE.match(order_str)
    if m:
        return {
            "type": "support_hold",
            "unit_type": m.group(1).upper(),
            "location": m.group(2).strip(),
            "coast": m.group(3),
            "supported_type": m.group(4).upper(),
            "supported_loc": m.group(5).strip(),
            "supported_coast": m.group(6),
        }

    # Move (also used for retreats)
    m = _MOVE_RE.match(order_str)
    if m:
        return {
            "type": "move",
            "unit_type": m.group(1).upper(),
            "location": m.group(2).strip(),
            "coast": m.group(3),
            "destination": m.group(4).strip(),
            "dest_coast": m.group(5),
        }

    # Hold
    m = _HOLD_RE.match(order_str)
    if m:
        return {
            "type": "hold",
            "unit_type": m.group(1).upper(),
            "location": m.group(2).strip(),
            "coast": m.group(3),
        }

    return None


def _unit_type_abbrev(unit_type):
    """Normalize unit type to single-letter abbreviation."""
    if unit_type in ("Army", "A"):
        return "A"
    if unit_type in ("Fleet", "F"):
        return "F"
    return unit_type


def _normalize_loc(location):
    """Normalize a location for comparison (strip whitespace, title case)."""
    return location.strip()


def _unit_matches(unit_dict, unit_type, location):
    """Check if a unit dict from game state matches a parsed order's unit."""
    ut = _unit_type_abbrev(unit_dict["type"])
    if ut != unit_type:
        return False
    # Compare base location (strip coast from game state location)
    state_loc = unit_dict["location"].split("(")[0].strip()
    order_loc = location.split("(")[0].strip()
    return state_loc == order_loc


# Phase categories for order type validation
_MOVEMENT_PHASES = {
    Phase.SPRING_MOVEMENT, Phase.FALL_MOVEMENT,
}
_DIPLOMACY_PHASES = {
    Phase.SPRING_DIPLOMACY, Phase.FALL_DIPLOMACY,
}
_RETREAT_PHASES = {
    Phase.SPRING_RETREAT, Phase.FALL_RETREAT,
}
_ADJUSTMENT_PHASES = {
    Phase.WINTER_ADJUSTMENT,
}

# Valid order types per phase category
_MOVEMENT_ORDER_TYPES = {"move", "hold", "support_move", "support_hold",
                         "convoy"}
_RETREAT_ORDER_TYPES = {"move", "retreat_disband"}
_ADJUSTMENT_ORDER_TYPES = {"build", "disband", "waive"}


def validate_order(order_str, power, state):
    """Validate a single order string against the game state.

    Checks:
    1. Order syntax is parseable
    2. Unit referenced belongs to the power (for movement/retreat)
    3. Order type matches the current phase

    Args:
        order_str: Raw order string from the agent.
        power: The power submitting the order.
        state: Current game state dict.

    Returns:
        Tuple of (is_valid, parsed_order_or_None, error_message_or_None).
    """
    parsed = parse_order(order_str)
    if parsed is None:
        return False, None, f"Cannot parse order: {order_str}"

    phase = Phase(state["phase"])

    # Phase-appropriate order type check
    order_type = parsed["type"]

    if phase in _MOVEMENT_PHASES:
        if order_type not in _MOVEMENT_ORDER_TYPES:
            return (False, parsed,
                    f"Order type '{order_type}' not valid in {phase.value}")
    elif phase in _RETREAT_PHASES:
        if order_type not in _RETREAT_ORDER_TYPES:
            return (False, parsed,
                    f"Order type '{order_type}' not valid in {phase.value}")
    elif phase in _ADJUSTMENT_PHASES:
        if order_type not in _ADJUSTMENT_ORDER_TYPES:
            return (False, parsed,
                    f"Order type '{order_type}' not valid in {phase.value}")
    elif phase in _DIPLOMACY_PHASES:
        # No orders during diplomacy phases (only negotiation)
        return (False, parsed,
                f"No orders accepted during {phase.value}")

    # Unit ownership check (for orders that reference a unit)
    if order_type in _MOVEMENT_ORDER_TYPES | {"retreat_disband"}:
        units = state["units"].get(power, [])
        unit_type = parsed["unit_type"]
        location = parsed["location"]
        if not any(_unit_matches(u, unit_type, location) for u in units):
            return (False, parsed,
                    f"{power} has no {unit_type} at {location}")

    # For retreat orders, also check the unit is in the dislodged list
    if phase in _RETREAT_PHASES and order_type in ("move", "retreat_disband"):
        dislodged = state.get("dislodged", [])
        unit_type = parsed["unit_type"]
        location = parsed["location"]
        found = False
        for d in dislodged:
            if d["power"] == power and _unit_matches(
                d["unit"], unit_type, location
            ):
                found = True
                break
        if not found:
            return (False, parsed,
                    f"{power} has no dislodged {unit_type} at {location}")

    return True, parsed, None


def validate_orders(order_strings, power, state):
    """Validate a list of order strings.

    Returns:
        Tuple of (valid_orders, errors) where valid_orders is a list
        of (order_str, parsed_dict) and errors is a list of
        (order_str, error_msg).
    """
    valid = []
    errors = []
    for order_str in order_strings:
        is_valid, parsed, error = validate_order(order_str, power, state)
        if is_valid:
            valid.append((order_str, parsed))
        else:
            errors.append((order_str, error))
    return valid, errors


def default_orders(power, state):
    """Generate default Hold orders for all units of a power.

    Used when an agent fails to submit valid orders.

    Args:
        power: Power name.
        state: Current game state.

    Returns:
        List of order strings (e.g., ["A Paris H", "F Brest H"]).
    """
    phase = Phase(state["phase"])
    units = state["units"].get(power, [])

    if phase in _MOVEMENT_PHASES:
        orders = []
        for unit in units:
            ut = _unit_type_abbrev(unit["type"])
            orders.append(f"{ut} {unit['location']} H")
        return orders

    if phase in _RETREAT_PHASES:
        # Default: disband all dislodged units
        dislodged = state.get("dislodged", [])
        orders = []
        for d in dislodged:
            if d["power"] == power:
                ut = _unit_type_abbrev(d["unit"]["type"])
                orders.append(f"{ut} {d['unit']['location']} Disband")
        return orders

    if phase in _ADJUSTMENT_PHASES:
        # Default: waive all builds, or no action
        return []

    return []


# --- Order file management ---

def _orders_dir(game_dir, year, phase):
    """Return the orders directory for a given year/phase."""
    game_dir = Path(game_dir)
    phase_label = phase.replace(" ", "_")
    d = game_dir / "orders" / str(year) / phase_label
    d.mkdir(parents=True, exist_ok=True)
    return d


def submit_orders(game_dir, power, year, phase, order_strings,
                  agent_gnupghome, gm_email="gm@perfid.local"):
    """Submit orders: encrypt with GM's public key, write to orders dir.

    The agent's keyring must have the GM's public key imported.

    Args:
        game_dir: Path to game directory.
        power: Power name.
        year: Game year.
        phase: Phase label (e.g. "Spring Movement").
        order_strings: List of order strings.
        agent_gnupghome: Path to the agent's GPG home (has GM pub key).
        gm_email: GM's email for encryption.

    Returns:
        Path to the written .gpg file.
    """
    orders_d = _orders_dir(game_dir, year, phase)

    payload = json.dumps({
        "power": power,
        "year": year,
        "phase": phase,
        "orders": order_strings,
    }, indent=2)

    output_path = str(orders_d / f"{power}.gpg")
    gpg_mod.encrypt_to_file(
        agent_gnupghome, payload, gm_email, output_path
    )
    return Path(output_path)


def decrypt_orders(game_dir, power, year, phase, gm_gnupghome):
    """GM decrypts a power's orders and writes the JSON file.

    Args:
        game_dir: Path to game directory.
        power: Power name.
        year: Game year.
        phase: Phase label.
        gm_gnupghome: Path to GM's GPG home (has private key).

    Returns:
        Parsed order dict, or None if no orders found.
    """
    orders_d = _orders_dir(game_dir, year, phase)
    gpg_path = orders_d / f"{power}.gpg"

    if not gpg_path.exists():
        return None

    plaintext = gpg_mod.decrypt_file(gm_gnupghome, str(gpg_path))
    order_data = json.loads(plaintext)

    # Write decrypted JSON for the record
    json_path = orders_d / f"{power}.json"
    json_path.write_text(json.dumps(order_data, indent=2) + "\n")

    return order_data


def decrypt_all_orders(game_dir, year, phase, gm_gnupghome):
    """Decrypt all submitted orders for a given year/phase.

    Args:
        game_dir: Path to game directory.
        year: Game year.
        phase: Phase label.
        gm_gnupghome: Path to GM's GPG home.

    Returns:
        Dict of power → order_data (or None for powers that didn't
        submit).
    """
    results = {}
    for power in POWERS:
        results[power] = decrypt_orders(
            game_dir, power, year, phase, gm_gnupghome
        )
    return results


def collect_orders(game_dir, year, phase, gm_gnupghome, state):
    """Decrypt, validate, and collect all orders for adjudication.

    For powers that didn't submit or submitted invalid orders,
    default Hold orders are generated.

    Args:
        game_dir: Path to game directory.
        year: Game year.
        phase: Phase label.
        gm_gnupghome: Path to GM's GPG home.
        state: Current game state.

    Returns:
        Dict with keys:
            "orders": {power: [order_strings]},
            "errors": {power: [(order_str, error_msg)]},
            "defaults": [powers that used default orders],
    """
    all_decrypted = decrypt_all_orders(game_dir, year, phase, gm_gnupghome)

    result = {
        "orders": {},
        "errors": {},
        "defaults": [],
    }

    for power in POWERS:
        if power in state.get("eliminated", []):
            result["orders"][power] = []
            continue

        order_data = all_decrypted.get(power)
        if order_data is None:
            # No submission — use defaults
            result["orders"][power] = default_orders(power, state)
            result["defaults"].append(power)
            continue

        order_strings = order_data.get("orders", [])
        valid, errors = validate_orders(order_strings, power, state)

        if errors:
            result["errors"][power] = errors

        if valid:
            result["orders"][power] = [o for o, _ in valid]
        else:
            # All orders invalid — use defaults
            result["orders"][power] = default_orders(power, state)
            result["defaults"].append(power)

    return result


def has_submitted(game_dir, power, year, phase):
    """Check if a power has already submitted orders."""
    orders_d = _orders_dir(game_dir, year, phase)
    return (orders_d / f"{power}.gpg").exists()


# --- Private notes ---

def _notes_dir(game_dir, power):
    """Return the notes directory for a power."""
    game_dir = Path(game_dir)
    d = game_dir / "notes" / power
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_note(game_dir, power, year, phase, note_text,
               agent_gnupghome, agent_email=None):
    """Write a private note encrypted with the agent's own key.

    Args:
        game_dir: Path to game directory.
        power: Power name.
        year: Game year.
        phase: Phase label.
        note_text: Plaintext note content.
        agent_gnupghome: Path to the agent's GPG home.
        agent_email: Agent's email. Defaults to <power>@perfid.local.

    Returns:
        Path to the written .gpg note file.
    """
    if agent_email is None:
        agent_email = f"{power.lower()}@perfid.local"

    notes_d = _notes_dir(game_dir, power)
    phase_label = phase.replace(" ", "_")
    filename = f"{year}-{phase_label}.gpg"
    output_path = str(notes_d / filename)

    gpg_mod.encrypt_to_file(
        agent_gnupghome, note_text, agent_email, output_path
    )
    return Path(output_path)


def read_note(game_dir, power, year, phase, agent_gnupghome):
    """Read a private note written by the agent.

    Args:
        game_dir: Path to game directory.
        power: Power name.
        year: Game year.
        phase: Phase label.
        agent_gnupghome: Path to the agent's GPG home.

    Returns:
        Decrypted note text, or None if no note exists.
    """
    notes_d = _notes_dir(game_dir, power)
    phase_label = phase.replace(" ", "_")
    gpg_path = notes_d / f"{year}-{phase_label}.gpg"

    if not gpg_path.exists():
        return None

    return gpg_mod.decrypt_file(agent_gnupghome, str(gpg_path))


def list_notes(game_dir, power):
    """List all notes for a power, sorted by year then phase order.

    Returns:
        List of dicts with year, phase, path for each note.
    """
    phase_index = {p.value: i for i, p in enumerate(PHASE_ORDER)}
    notes_d = _notes_dir(game_dir, power)
    notes = []

    for f in notes_d.iterdir():
        if not f.name.endswith(".gpg"):
            continue
        # Parse filename: <year>-<phase>.gpg
        base = f.name.removesuffix(".gpg")
        parts = base.split("-", 1)
        if len(parts) != 2:
            continue
        try:
            year = int(parts[0])
        except ValueError:
            continue
        phase = parts[1].replace("_", " ")
        notes.append({
            "year": year,
            "phase": phase,
            "path": str(f),
        })

    notes.sort(key=lambda n: (n["year"], phase_index.get(n["phase"], 99)))
    return notes
