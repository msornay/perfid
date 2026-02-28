"""Thin adapter for jDip headless CLI.

Translates between perfid's game state JSON and jDip's CLI protocol.
jDip provides DATC-compliant adjudication for Diplomacy orders.

Usage:
    from jdip_adapter import jdip_init, jdip_adjudicate

    # Get initial standard game state from jDip
    state = jdip_init()

    # Adjudicate orders
    result = jdip_adjudicate(phase, units, sc_ownership, orders)
"""

import json
import subprocess
from pathlib import Path

# jDip directory relative to this file
_JDIP_DIR = Path(__file__).parent / "jdip"
_JDIP_JAR = _JDIP_DIR / "jdip-headless.jar"
_JDIP_LIB = _JDIP_DIR / "lib"

# Phase brief name mapping: perfid phase labels -> jDip 6-char codes
_PHASE_MAP = {
    "Spring Diplomacy": "S{year}M",
    "Spring Movement": "S{year}M",
    "Spring Retreat": "S{year}R",
    "Fall Diplomacy": "F{year}M",
    "Fall Movement": "F{year}M",
    "Fall Retreat": "F{year}R",
    "Winter Adjustment": "F{year}B",
}


def _classpath():
    """Build the Java classpath string."""
    jars = list(_JDIP_LIB.glob("*.jar"))
    return str(_JDIP_JAR) + ":" + ":".join(str(j) for j in jars)


def _run_jdip(command, stdin_data=None):
    """Run the jDip CLI and return parsed JSON output.

    Args:
        command: CLI command ("init" or "adjudicate").
        stdin_data: Optional JSON string to pass via stdin.

    Returns:
        Parsed JSON dict from jDip stdout.

    Raises:
        RuntimeError: If jDip exits with non-zero status.
    """
    cmd = [
        "java",
        "-Djava.awt.headless=true",
        "-cp", _classpath(),
        "dip.misc.JDipCLI",
        command,
    ]

    result = subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        cwd=str(_JDIP_DIR),
        timeout=30,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"jDip {command} failed (exit {result.returncode}):\n"
            f"{result.stderr}"
        )

    return json.loads(result.stdout)


def jdip_init():
    """Get the initial standard Diplomacy game state from jDip.

    Returns:
        Dict with keys: phase, year, units, sc_ownership,
        home_centers, all_supply_centers, powers.
    """
    return _run_jdip("init")


def phase_to_brief(phase_label, year):
    """Convert a perfid phase label to a jDip 6-char brief code.

    Args:
        phase_label: e.g. "Spring Movement", "Fall Retreat".
        year: e.g. 1901.

    Returns:
        6-char string like "S1901M".
    """
    template = _PHASE_MAP.get(phase_label)
    if template is None:
        raise ValueError(f"Unknown phase: {phase_label}")
    return template.format(year=year)


def _units_to_jdip(units):
    """Convert perfid units dict to jDip format.

    perfid format: {"France": [{"type": "Army", "location": "Paris"}]}
    jDip format: same structure, but location names may need
    normalization (e.g. "St Petersburg" -> "St. Petersburg").
    """
    result = {}
    for power, unit_list in units.items():
        jdip_units = []
        for unit in unit_list:
            loc = unit["location"]
            # jDip uses "St. Petersburg" with a period
            loc = loc.replace("St Petersburg", "St. Petersburg")
            jdip_units.append({
                "type": unit["type"],
                "location": loc,
            })
        result[power] = jdip_units
    return result


def _sc_to_jdip(sc_ownership):
    """Convert perfid SC ownership to jDip format."""
    result = {}
    for sc, owner in sc_ownership.items():
        sc_name = sc.replace("St Petersburg", "St. Petersburg")
        result[sc_name] = owner
    return result


def _units_from_jdip(units):
    """Convert jDip unit format back to perfid format."""
    result = {}
    for power, unit_list in units.items():
        perfid_units = []
        for unit in unit_list:
            loc = unit["location"]
            loc = loc.replace("St. Petersburg", "St Petersburg")
            perfid_units.append({
                "type": unit["type"],
                "location": loc,
            })
        result[power] = perfid_units
    return result


def _sc_from_jdip(sc_ownership):
    """Convert jDip SC ownership back to perfid format."""
    result = {}
    for sc, owner in sc_ownership.items():
        sc_name = sc.replace("St. Petersburg", "St Petersburg")
        result[sc_name] = owner
    return result


def _orders_to_jdip(orders):
    """Convert perfid order strings to jDip-compatible format.

    perfid format: {"France": ["A Paris - Burgundy", "F Brest H"]}
    jDip expects similar format but the power prefix is added
    by JDipCLI internally.
    """
    result = {}
    for power, order_list in orders.items():
        jdip_orders = []
        for order in order_list:
            # Normalize location names for jDip
            o = order.replace("St Petersburg", "St. Petersburg")
            jdip_orders.append(o)
        result[power] = jdip_orders
    return result


def jdip_adjudicate(phase_label, year, units, sc_ownership, orders):
    """Adjudicate orders using jDip.

    Args:
        phase_label: Perfid phase label (e.g. "Spring Movement").
        year: Game year (e.g. 1901).
        units: Dict of power -> list of unit dicts.
        sc_ownership: Dict of SC name -> owner power name.
        orders: Dict of power -> list of order strings.

    Returns:
        Dict with keys:
            resolved_units: {power: [unit_dicts]}
            dislodged: [{power, unit, retreats}]
            sc_ownership: {sc: owner}
            next_phase: brief phase code
            next_phase_long: human-readable phase
            next_year: int
            results: [result strings]
            order_results: [{order, result, message}]
            game_over: bool
    """
    brief = phase_to_brief(phase_label, year)

    payload = json.dumps({
        "phase": brief,
        "units": _units_to_jdip(units),
        "sc_ownership": _sc_to_jdip(sc_ownership),
        "orders": _orders_to_jdip(orders),
    })

    result = _run_jdip("adjudicate", stdin_data=payload)

    # Normalize location names back to perfid format
    if "resolved_units" in result:
        result["resolved_units"] = _units_from_jdip(
            result["resolved_units"]
        )
    if "dislodged" in result:
        for d in result["dislodged"]:
            if "unit" in d:
                loc = d["unit"]["location"]
                d["unit"]["location"] = loc.replace(
                    "St. Petersburg", "St Petersburg"
                )
    if "sc_ownership" in result:
        result["sc_ownership"] = _sc_from_jdip(
            result["sc_ownership"]
        )

    return result


def simulate(state, orders):
    """Simulate order adjudication without modifying game state.

    Intended for agents to test order combinations and evaluate
    strategies before committing. Returns the full jDip result
    without advancing the game.

    Args:
        state: Current game state dict (not modified).
        orders: Dict of power -> list of order strings.

    Returns:
        Dict with jDip adjudication results (same as
        jdip_adjudicate output), plus a "summary" key with
        per-power unit counts and SC changes.
    """
    phase_label = state["phase"]
    year = state["year"]

    result = jdip_adjudicate(
        phase_label, year,
        state["units"],
        state["sc_ownership"],
        orders,
    )

    # Add summary for quick evaluation
    summary = {}
    for power in state["units"]:
        old_units = len(state["units"].get(power, []))
        new_units = len(
            result.get("resolved_units", {}).get(power, [])
        )
        old_scs = sum(
            1 for v in state["sc_ownership"].values()
            if v == power
        )
        new_scs = sum(
            1 for v in result.get("sc_ownership", {}).values()
            if v == power
        )
        summary[power] = {
            "units_before": old_units,
            "units_after": new_units,
            "scs_before": old_scs,
            "scs_after": new_scs,
        }
    result["summary"] = summary

    return result


def order_results_for(result, power):
    """Extract order results for a specific power.

    Args:
        result: Dict returned by jdip_adjudicate or simulate.
        power: Power name (e.g. "France").

    Returns:
        List of dicts with keys: order, result, message.
        Only includes orders belonging to the given power.
    """
    prefix = f"{power}: "
    return [
        r for r in result.get("order_results", [])
        if r.get("order", "").startswith(prefix)
    ]


def is_available():
    """Check if jDip is available (JAR and Java exist)."""
    if not _JDIP_JAR.exists():
        return False
    try:
        subprocess.run(
            ["java", "-version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
