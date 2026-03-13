#!/usr/bin/env python3
"""Render per-phase PNG map images from a perfid JSONL game log."""

import argparse
import json
import os
import sys

from map_renderer.map import (
    AUSTRIA, ENGLAND, FRANCE, GERMANY, ITALY, RUSSIA, TURKEY,
    army_hold, context, fleet_hold, reset, _set, set_color, write_substitution_image,
)
from map_renderer.data import COLOR_NEUTRAL

# perfid full province name → diplomacy-mapper abbreviation key
NAME_TO_ABBR = {
    # Austria home
    "Bohemia": "boh",
    "Budapest": "bud",
    "Galicia": "gal",
    "Trieste": "tri",
    "Tyrolia": "trl",
    "Vienna": "vie",
    # England home
    "Clyde": "cly",
    "Edinburgh": "edi",
    "Liverpool": "lvp",
    "London": "lon",
    "Wales": "wal",
    "Yorkshire": "yor",
    # France home
    "Brest": "bre",
    "Burgundy": "bur",
    "Gascony": "gas",
    "Marseilles": "mar",
    "Paris": "par",
    "Picardy": "pic",
    # Germany home
    "Berlin": "ber",
    "Kiel": "kie",
    "Munich": "mun",
    "Prussia": "pru",
    "Ruhr": "ruh",
    "Silesia": "sil",
    # Italy home
    "Apulia": "apu",
    "Naples": "nap",
    "Piedmont": "pie",
    "Rome": "rom",
    "Tuscany": "tus",
    "Venice": "ven",
    # Russia home
    "Finland": "fin",
    "Livonia": "lvn",
    "Moscow": "mos",
    "Sevastopol": "sev",
    "St Petersburg": "stp",
    "Ukraine": "ukr",
    "Warsaw": "war",
    # Turkey home
    "Ankara": "ank",
    "Armenia": "arm",
    "Constantinople": "con",
    "Smyrna": "smy",
    "Syria": "syr",
    # Neutral / unaligned
    "Albania": "alb",
    "Belgium": "bel",
    "Bulgaria": "bul",
    "Denmark": "den",
    "Greece": "gre",
    "Holland": "hol",
    "Norway": "nor",
    "Portugal": "por",
    "Rumania": "rum",
    "Serbia": "ser",
    "Spain": "spa",
    "Sweden": "swe",
    "Tunis": "tun",
    "North Africa": "naf",
    # Coasts
    "St Petersburg (South Coast)": "stp_sc",
    "St Petersburg (North Coast)": "stp_nc",
    "Spain (South Coast)": "spa_sc",
    "Spain (North Coast)": "spa_nc",
    "Bulgaria (South Coast)": "bul_sc",
    "Bulgaria (East Coast)": "bul_sc",
    "Bulgaria (North Coast)": "bul_nc",
    # Sea zones
    "Adriatic Sea": "adr",
    "Aegean Sea": "aeg",
    "Baltic Sea": "bal",
    "Barents Sea": "bar",
    "Black Sea": "bla",
    "Eastern Mediterranean": "eas",
    "English Channel": "eng",
    "Gulf of Bothnia": "bot",
    "Gulf of Lyon": "lyo",
    "Helgoland Bight": "hel",
    "Ionian Sea": "ion",
    "Irish Sea": "iri",
    "Mid-Atlantic Ocean": "mao",
    "North Atlantic Ocean": "nao",
    "North Sea": "nth",
    "Norwegian Sea": "nwg",
    "Skagerrak": "ska",
    "Tyrrhenian Sea": "tyr",
    "Western Mediterranean": "wes",
}

POWER_TO_NATION = {
    "Austria": AUSTRIA,
    "England": ENGLAND,
    "France": FRANCE,
    "Germany": GERMANY,
    "Italy": ITALY,
    "Russia": RUSSIA,
    "Turkey": TURKEY,
}

# All supply center abbreviations (for coloring)
SC_ABBRS = {
    "ank", "bel", "ber", "bre", "bud", "bul", "con", "den", "edi",
    "gre", "hol", "kie", "lon", "lvp", "mar", "mos", "mun", "nap",
    "nor", "par", "por", "rom", "rum", "ser", "sev", "smy", "spa",
    "stp", "swe", "tri", "tun", "ven", "vie", "war",
}


def translate(name):
    """Translate a perfid province name to a diplomacy-mapper abbreviation."""
    abbr = NAME_TO_ABBR.get(name)
    if abbr is None:
        print(f"Warning: unknown province '{name}', skipping", file=sys.stderr)
    return abbr


def render_phase(units, sc_ownership, output_path):
    """Render a single phase to a PNG file.

    units: {power: [{type, location}]}
    sc_ownership: {sc_name: power_or_null}
    """
    reset()

    # Color supply centers by owner
    for sc_name, owner in sc_ownership.items():
        abbr = translate(sc_name)
        if abbr is None:
            continue
        if owner and owner in POWER_TO_NATION:
            nation = POWER_TO_NATION[owner]
            context(nation)
            _set(abbr)
        else:
            set_color(abbr, COLOR_NEUTRAL)

    # Place units
    for power, power_units in units.items():
        if power not in POWER_TO_NATION:
            continue
        nation = POWER_TO_NATION[power]
        context(nation)
        for unit in power_units:
            abbr = translate(unit["location"])
            if abbr is None:
                continue
            if unit["type"] == "Army":
                army_hold(abbr)
            else:
                fleet_hold(abbr)

    write_substitution_image(output_path)


def collect_phases(jsonl_path):
    """Parse JSONL log and extract per-phase state snapshots.

    Returns list of (label, units, sc_ownership) tuples.
    """
    phases = []
    # Seed with starting home SC ownership
    cumulative_sc = {
        "Vienna": "Austria", "Budapest": "Austria", "Trieste": "Austria",
        "London": "England", "Edinburgh": "England", "Liverpool": "England",
        "Brest": "France", "Paris": "France", "Marseilles": "France",
        "Berlin": "Germany", "Kiel": "Germany", "Munich": "Germany",
        "Naples": "Italy", "Rome": "Italy", "Venice": "Italy",
        "St Petersburg": "Russia", "Moscow": "Russia",
        "Warsaw": "Russia", "Sevastopol": "Russia",
        "Ankara": "Turkey", "Constantinople": "Turkey", "Smyrna": "Turkey",
    }

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            event = record.get("event")

            if event == "adjudication":
                year = record["year"]
                phase = record["phase"]
                units = record["resolved_units"]
                # Apply SC changes
                for sc, change in record.get("sc_changes", {}).items():
                    cumulative_sc[sc] = change.get("to")
                label = f"{year}-{phase}"
                phases.append((label, units, dict(cumulative_sc)))

            elif event == "retreats_applied":
                year = record["year"]
                phase = record["phase"]
                units = record["units"]
                label = f"{year}-{phase}"
                phases.append((label, units, dict(cumulative_sc)))

            elif event == "adjustments_applied":
                year = record.get("year")
                phase = record.get("phase")
                units = record.get("units")
                if year and units:
                    label = f"{year}-{phase}"
                    phases.append((label, units, dict(cumulative_sc)))

    return phases


def sanitize_filename(label):
    """Convert phase label to safe filename."""
    return label.replace(" ", "_").replace("/", "-")


def main():
    parser = argparse.ArgumentParser(description="Render perfid game maps")
    parser.add_argument("jsonl", help="Path to JSONL game log")
    parser.add_argument("--output-dir", default="renders",
                        help="Output directory for PNGs")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    phases = collect_phases(args.jsonl)
    if not phases:
        print("No phases found in log.", file=sys.stderr)
        sys.exit(1)

    for i, (label, units, sc_ownership) in enumerate(phases):
        filename = f"{i:03d}_{sanitize_filename(label)}.png"
        output_path = os.path.join(args.output_dir, filename)
        render_phase(units, sc_ownership, output_path)
        print(f"Rendered {output_path}")


if __name__ == "__main__":
    main()
