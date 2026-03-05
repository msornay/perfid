"""Game orchestration for perfid — runs inside the container.

Implements the main game loop, phase handlers, GPG setup/cleanup,
agent execution, and message routing. No Docker subprocess calls —
everything runs directly in the container.

perfid runs as root (manages GPG keys), calls claude as the player
user via subprocess.run(['sudo', '-u', 'player', ...]).
"""

import json
import os
import random
import signal
import shutil
import subprocess
import sys
import time

import gpg as gpg_mod
from game_state import (
    POWERS,
    Phase,
    adjudicate,
    adjustment_counts,
    apply_adjustments,
    apply_retreats,
    format_status,
    load_state,
    load_sessions,
    get_session_id,
    mark_session_started,
    next_phase,
    save_state,
)
from logger import GameLogger
from message_router import (
    archive_phase,
    route_messages,
)
from orders import (
    collect_from_dropbox,
    collect_orders,
    has_submitted,
)
from prompt import system_prompt, turn_context

# Minimum negotiation rounds before order submission is allowed
MIN_NEGOTIATION_ROUNDS = int(os.environ.get("PERFID_MIN_ROUNDS", "2"))

# Maximum total rounds per movement phase (safety limit)
MAX_ROUNDS = 10

# Sidecar file for agent simulation logs (written by jdip_adapter.simulate)
SIM_LOG_PATH = "/tmp/perfid-simulations.jsonl"

# Player home directory and credential paths
PLAYER_HOME = "/home/player"
PLAYER_CLAUDE = os.path.join(PLAYER_HOME, ".claude")
CREDENTIALS_SRC = "/root/.claude-credentials"


class PerfidError(Exception):
    """Clean error messages for the CLI."""
    pass


def _sigterm_handler(signum, frame):
    """Convert SIGTERM into KeyboardInterrupt.

    Docker sends SIGTERM on container stop. Raising KeyboardInterrupt
    lets the game loop exit through the same cleanup path as Ctrl+C.
    """
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, _sigterm_handler)


def _remove_stale_gpg_sockets(gpg_dir):
    """Remove stale gpg-agent socket files from a GPG home directory.

    When a GPG keyring is created in one container process and used
    in another, the agent socket files are stale and prevent
    gpg-agent from starting. Removing them lets GPG start a fresh
    agent.
    """
    if not os.path.isdir(gpg_dir):
        return
    for entry in os.listdir(gpg_dir):
        if entry.startswith("S.gpg-agent"):
            try:
                os.remove(os.path.join(gpg_dir, entry))
            except OSError:
                pass


def _log(logger, event, **kwargs):
    """Write a structured JSONL event to the game log."""
    logger._event(event, **kwargs)


def _active_powers(state):
    """Return list of non-eliminated powers."""
    return [p for p in POWERS if p not in state.get("eliminated", [])]


def _dropbox_path(power):
    """Return the per-power order dropbox path."""
    return f"/tmp/orders-{power.lower()}"


# --- Phase checkpoint (resume after Ctrl+C) ---


def _save_checkpoint(game_dir, year, phase, round_num, power_order,
                     next_index):
    """Save in-phase progress so the game can resume after interruption."""
    path = os.path.join(game_dir, ".phase-progress.json")
    tmp = path + ".tmp"
    data = {"year": year, "phase": phase, "round": round_num,
            "power_order": power_order, "next_index": next_index}
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _load_checkpoint(game_dir, year, phase):
    """Load checkpoint if it matches the current year/phase."""
    path = os.path.join(game_dir, ".phase-progress.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        cp = json.load(f)
    if cp.get("year") == year and cp.get("phase") == phase:
        return cp
    return None


def _clear_checkpoint(game_dir):
    """Remove the checkpoint file after a phase completes."""
    path = os.path.join(game_dir, ".phase-progress.json")
    try:
        os.unlink(path)
    except OSError:
        pass


# --- GPG isolation ---


def setup_turn_gpg(ctx, power):
    """Prepare GPG keyring and decrypt memory for a power's turn.

    1. Create temp GNUPGHOME with the power's private key
    2. Import all public keys and trust them
    3. Decrypt the power's encrypted .claude/ archive if it exists
    4. Re-inject shared credentials (API auth)
    5. chown everything to player

    Args:
        ctx: Game context dict.
        power: Diplomacy power name.
    """
    game_dir = ctx["game_dir"]
    gm_gnupghome = ctx["gm_gnupghome"]
    logger = ctx["logger"]
    lc_power = power.lower()
    gpg_dir = f"/tmp/gpg-{lc_power}"

    # Remove stale simulation sidecar file (crash recovery)
    try:
        os.unlink(SIM_LOG_PATH)
    except OSError:
        pass

    # Remove stale agent sockets from GM keyring (may be from
    # a different container process that ran 'perfid new')
    _remove_stale_gpg_sockets(gm_gnupghome)

    # Create temp GNUPGHOME
    os.makedirs(gpg_dir, mode=0o700, exist_ok=True)

    # Decrypt private key with GM key and import
    key_file = os.path.join(game_dir, "keys", f"{power}.key.gpg")
    if not os.path.exists(key_file):
        raise PerfidError(
            f"Key file missing for {power}: {key_file}\n"
            f"Run 'perfid new' to generate keys."
        )
    private_key_armor = gpg_mod.decrypt_file(gm_gnupghome, key_file)
    gpg_mod.import_key(gpg_dir, private_key_armor)

    # Import all public keys and trust
    gpg_mod.import_all_pubkeys(gpg_dir, game_dir)

    # chown gpg dir to player
    subprocess.run(
        ["chown", "-R", "player:player", gpg_dir],
        check=True, capture_output=True,
    )

    # Symlink ~/.gnupg for bare gpg commands
    gnupg_link = os.path.join(PLAYER_HOME, ".gnupg")
    if os.path.islink(gnupg_link) or os.path.exists(gnupg_link):
        os.remove(gnupg_link)
    os.symlink(gpg_dir, gnupg_link)
    subprocess.run(
        ["chown", "-h", "player:player", gnupg_link],
        check=True, capture_output=True,
    )

    # Wipe any leftover .claude (safety — clear contents, not rmtree,
    # because the path may be a Docker volume mount point)
    if os.path.isdir(PLAYER_CLAUDE):
        for entry in os.listdir(PLAYER_CLAUDE):
            p = os.path.join(PLAYER_CLAUDE, entry)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass

    # Decrypt memory archive if it exists
    enc_dir = os.path.join(game_dir, ".encrypted")
    enc_file = os.path.join(enc_dir, f"{lc_power}.tar.gpg")
    if os.path.exists(enc_file):
        dec = subprocess.run(
            ["gpg", "--batch", "--yes", "--pinentry-mode", "loopback",
             "--homedir", gpg_dir, "--decrypt", enc_file],
            capture_output=True,
        )
        if dec.returncode == 0 and dec.stdout:
            subprocess.run(
                ["tar", "xf", "-", "-C", PLAYER_HOME],
                input=dec.stdout,
                check=True, capture_output=True,
            )

    # Re-inject shared credentials
    if os.path.isdir(CREDENTIALS_SRC):
        os.makedirs(PLAYER_CLAUDE, exist_ok=True)
        for fname in os.listdir(CREDENTIALS_SRC):
            src = os.path.join(CREDENTIALS_SRC, fname)
            dst = os.path.join(PLAYER_CLAUDE, fname)
            if os.path.isfile(src):
                shutil.copy2(src, dst)

    # Ensure player owns everything
    if os.path.isdir(PLAYER_CLAUDE):
        subprocess.run(
            ["chown", "-R", "player:player", PLAYER_CLAUDE],
            check=True, capture_output=True,
        )

    _log(logger, "gpg_setup", power=power, action="keys_injected")


def cleanup_turn_gpg(ctx, power):
    """Encrypt memory and remove GPG keys after a power's turn.

    1. Encrypt entire ~player/.claude/ with the power's key
    2. Wipe plaintext .claude/
    3. Remove temp GNUPGHOME

    Args:
        ctx: Game context dict.
        power: Diplomacy power name.
    """
    game_dir = ctx["game_dir"]
    logger = ctx["logger"]
    lc_power = power.lower()
    gpg_dir = f"/tmp/gpg-{lc_power}"
    key_email = f"{lc_power}@perfid.local"
    enc_dir = os.path.join(game_dir, ".encrypted")

    os.makedirs(enc_dir, exist_ok=True)
    memory_encrypted = False

    # Encrypt entire .claude directory
    if os.path.isdir(PLAYER_CLAUDE):
        tar = subprocess.run(
            ["tar", "cf", "-", "-C", PLAYER_HOME, ".claude"],
            capture_output=True,
        )
        if tar.returncode == 0 and tar.stdout:
            enc_file = os.path.join(enc_dir, f"{lc_power}.tar.gpg")
            enc = subprocess.run(
                ["gpg", "--batch", "--yes", "--homedir", gpg_dir,
                 "--trust-model", "always",
                 "--encrypt", "--recipient", key_email,
                 "-o", enc_file],
                input=tar.stdout,
                capture_output=True,
            )
            memory_encrypted = enc.returncode == 0

    # Wipe plaintext .claude (clear contents, not rmtree,
    # because the path may be a Docker volume mount point)
    if os.path.isdir(PLAYER_CLAUDE):
        for entry in os.listdir(PLAYER_CLAUDE):
            p = os.path.join(PLAYER_CLAUDE, entry)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass

    # Remove temp GNUPGHOME
    shutil.rmtree(gpg_dir, ignore_errors=True)

    # Remove symlink
    gnupg_link = os.path.join(PLAYER_HOME, ".gnupg")
    if os.path.islink(gnupg_link):
        os.remove(gnupg_link)

    _log(logger, "gpg_cleanup", power=power,
         memory_encrypted=memory_encrypted)


# --- Agent execution ---


def _collect_simulation_log(ctx, power):
    """Read simulation sidecar file, encrypt and log each record, delete file.

    Args:
        ctx: Game context dict.
        power: Diplomacy power name.
    """
    if not os.path.exists(SIM_LOG_PATH):
        return

    gm_gnupghome = ctx["gm_gnupghome"]
    logger = ctx["logger"]

    try:
        with open(SIM_LOG_PATH) as f:
            lines = f.readlines()
    except OSError:
        return

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue

        try:
            encrypted = gpg_mod.encrypt(
                gm_gnupghome, line, "gm@perfid.local"
            )
        except Exception:
            encrypted = "(encryption failed)"

        logger.simulation_run(
            power=power,
            phase=record.get("phase", ""),
            year=record.get("year", 0),
            encrypted_data=encrypted,
        )

    try:
        os.unlink(SIM_LOG_PATH)
    except OSError:
        pass


def run_agent(ctx, power, prompt, env_extra=None):
    """Run the Claude agent for a power's turn.

    Calls claude as the player user via sudo. Captures stdout,
    encrypts it with the GM key, and logs the encrypted output.

    Args:
        ctx: Game context dict.
        power: Diplomacy power name.
        prompt: The turn prompt string.
        env_extra: Optional dict of additional env vars to pass
            to the agent subprocess (e.g. PERFID_DROPBOX).

    Returns:
        True if the agent exited successfully, False otherwise.
    """
    game_dir = ctx["game_dir"]
    gm_gnupghome = ctx["gm_gnupghome"]
    logger = ctx["logger"]
    lc_power = power.lower()
    gpg_dir = f"/tmp/gpg-{lc_power}"

    # Build env vars list for the agent
    env_vars = [
        f"GNUPGHOME={gpg_dir}",
        f"PERFID_GAME_DIR={game_dir}",
        f"PERFID_SIM_LOG={SIM_LOG_PATH}",
    ]
    if env_extra:
        for k, v in env_extra.items():
            env_vars.append(f"{k}={v}")

    session_id = get_session_id(game_dir, power)
    sessions = load_sessions(game_dir)
    started = sessions.get(power, {}).get("started", False)

    if not started:
        sys_prompt = system_prompt(power)
        cmd = [
            "sudo", "-u", "player",
            "env", *env_vars,
            "claude", "--session-id", session_id,
            "-p", sys_prompt, "-p", prompt,
            "--dangerously-skip-permissions", "--verbose",
        ]
    else:
        cmd = [
            "sudo", "-u", "player",
            "env", *env_vars,
            "claude", "--resume", session_id,
            "-p", prompt,
            "--dangerously-skip-permissions", "--verbose",
        ]

    _log(logger, "agent_start", power=power, session=session_id)

    outpath = f"/tmp/perfid-agent-{lc_power}.out"
    try:
        with open(outpath, "w") as f:
            proc = subprocess.Popen(
                cmd, stdout=f, start_new_session=True,
            )
        try:
            while proc.poll() is None:
                time.sleep(0.5)
        except KeyboardInterrupt:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        with open(outpath) as f:
            output = f.read()
    finally:
        try:
            os.unlink(outpath)
        except OSError:
            pass
    returncode = proc.returncode

    if not started:
        mark_session_started(game_dir, power)

    # Encrypt agent output with GM key and log
    try:
        encrypted_output = gpg_mod.encrypt(
            gm_gnupghome, output, "gm@perfid.local"
        )
    except Exception:
        encrypted_output = "(encryption failed)"

    _log(logger, "agent_turn", power=power,
         encrypted_output=encrypted_output)

    # Collect simulation sidecar log (if agent ran any simulations)
    _collect_simulation_log(ctx, power)

    # Print output to stdout for live monitoring
    if output:
        print(output, end="", flush=True)

    _log(logger, "agent_done", power=power,
         submitted_orders=False)  # updated by caller

    if returncode != 0:
        raise PerfidError(
            f"Agent for {power} exited with code "
            f"{returncode}"
        )

    return True


def route_and_log_messages(ctx, power, state):
    """Route messages from a power's outbox and log them.

    Args:
        ctx: Game context dict.
        power: Diplomacy power name.
        state: Current game state dict.
    """
    game_dir = ctx["game_dir"]
    logger = ctx["logger"]

    routed = route_messages(game_dir, power)

    for src, dest in routed:
        from message_router import parse_message_filename
        parsed = parse_message_filename(os.path.basename(str(src)))
        if not parsed:
            continue

        # Read the encrypted message content for logging
        try:
            with open(str(src), "r") as f:
                encrypted = f.read()
        except Exception:
            encrypted = ""

        _log(logger, "message_routed",
             sender=parsed["sender"],
             recipient=parsed["recipient"],
             encrypted=encrypted)


# --- Phase handlers ---


def _movement_phase(ctx, state):
    """Handle a Spring or Fall movement phase.

    Runs multiple rounds of negotiation followed by order submission.
    Rounds 1-3 are negotiation-only. Round 4+ allows order submission.
    All powers play in every round (random order). Rounds continue
    until all powers have submitted or max_rounds is reached.

    Args:
        ctx: Game context dict.
        state: Current game state dict.
    """
    game_dir = ctx["game_dir"]
    logger = ctx["logger"]
    year = state["year"]
    phase = state["phase"]

    active = _active_powers(state)

    # Make orders directory root-only so agents can't peek
    orders_base = os.path.join(game_dir, "orders")
    os.makedirs(orders_base, exist_ok=True)
    try:
        os.chmod(orders_base, 0o700)
    except OSError:
        pass  # may fail in test environment

    cp = _load_checkpoint(game_dir, year, phase)
    start_round = cp["round"] if cp else 1
    start_index = cp["next_index"] if cp else 0
    saved_order = cp["power_order"] if cp else None

    if cp:
        skipped = cp['power_order'][:cp['next_index']]
        print(f"  Resuming round {cp['round']} — "
              f"skipping {', '.join(skipped)} "
              f"(already played)")

    for round_num in range(start_round, MAX_ROUNDS + 1):
        can_submit = round_num > MIN_NEGOTIATION_ROUNDS

        # Create per-power dropboxes when orders are allowed
        if can_submit:
            for p in active:
                dropbox = _dropbox_path(p)
                os.makedirs(dropbox, mode=0o700, exist_ok=True)
                try:
                    subprocess.run(
                        ["chown", "player:player", dropbox],
                        check=True, capture_output=True,
                    )
                except (subprocess.CalledProcessError, FileNotFoundError):
                    pass

        # Use saved order for resumed round, otherwise shuffle
        if saved_order and round_num == start_round:
            order = saved_order
        else:
            order = list(active)
            random.shuffle(order)
            start_index = 0

        for i, power in enumerate(order):
            if i < start_index:
                continue

            print(f"\n  === {power.upper()} (round {round_num}) ===")

            setup_turn_gpg(ctx, power)

            try:
                dropbox = _dropbox_path(power) if can_submit else None
                prompt = turn_context(
                    power, state, game_dir,
                    round_num=round_num,
                    dropbox=dropbox,
                    min_negotiation_rounds=MIN_NEGOTIATION_ROUNDS,
                )

                env_extra = {}
                if dropbox:
                    env_extra["PERFID_DROPBOX"] = dropbox
                run_agent(ctx, power, prompt, env_extra=env_extra)
                route_and_log_messages(ctx, power, state)

                # Collect orders from dropbox
                if can_submit:
                    collected = collect_from_dropbox(
                        game_dir, power, year, phase,
                        _dropbox_path(power),
                    )
                    if collected:
                        print(f"    {power} submitted orders.")
                        _log(logger, "orders_collected",
                             power=power)
            finally:
                cleanup_turn_gpg(ctx, power)

            _save_checkpoint(game_dir, year, phase,
                             round_num, order, i + 1)

        saved_order = None

        # Check if all have submitted (only after negotiation rounds)
        if can_submit:
            all_submitted = all(
                has_submitted(game_dir, p, year, phase)
                for p in active
            )
            if all_submitted:
                print("  All powers have submitted orders.")
                break
    else:
        # max_rounds reached — log and continue
        print(f"  Round limit reached ({MAX_ROUNDS}). "
              f"Using default orders for non-submitters.")

    _clear_checkpoint(game_dir)

    # Adjudicate
    adjudicate_movement(ctx, state)


def _retreat_phase(ctx, state):
    """Handle a Spring Retreat or Fall Retreat phase.

    Only powers with dislodged units need to act.

    Args:
        ctx: Game context dict.
        state: Current game state dict.
    """
    game_dir = ctx["game_dir"]
    year = state["year"]
    phase = state["phase"]
    dislodged = state.get("dislodged", [])

    active = _active_powers(state)

    powers_to_act = [
        p for p in active
        if any(d["power"] == p for d in dislodged)
    ]

    cp = _load_checkpoint(game_dir, year, phase)
    start_index = cp["next_index"] if cp else 0

    if cp:
        skipped = powers_to_act[:cp['next_index']]
        if skipped:
            print(f"  Resuming — skipping {', '.join(skipped)} "
                  f"(already played)")

    for i, power in enumerate(powers_to_act):
        if i < start_index:
            continue

        print(f"\n  === {power.upper()} — retreat ===")

        dropbox = _dropbox_path(power)
        os.makedirs(dropbox, mode=0o700, exist_ok=True)
        try:
            subprocess.run(
                ["chown", "player:player", dropbox],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        setup_turn_gpg(ctx, power)
        try:
            prompt = turn_context(
                power, state, game_dir, dropbox=dropbox,
            )
            run_agent(
                ctx, power, prompt,
                env_extra={"PERFID_DROPBOX": dropbox},
            )
            collect_from_dropbox(
                game_dir, power, year, phase, dropbox,
            )
        finally:
            cleanup_turn_gpg(ctx, power)

        _save_checkpoint(game_dir, year, phase,
                         1, powers_to_act, i + 1)

    _clear_checkpoint(game_dir)

    # Apply retreats and advance phase
    apply_retreats_and_advance(ctx, state)


def _winter_phase(ctx, state):
    """Handle the Winter Adjustment phase.

    All non-eliminated powers submit builds/disbands.

    Args:
        ctx: Game context dict.
        state: Current game state dict.
    """
    game_dir = ctx["game_dir"]
    year = state["year"]
    phase = state["phase"]

    active = _active_powers(state)

    cp = _load_checkpoint(game_dir, year, phase)
    start_index = cp["next_index"] if cp else 0

    if cp:
        skipped = active[:cp['next_index']]
        if skipped:
            print(f"  Resuming — skipping {', '.join(skipped)} "
                  f"(already played)")

    for i, power in enumerate(active):
        if i < start_index:
            continue

        print(f"\n  === {power.upper()} — adjustments ===")

        dropbox = _dropbox_path(power)
        os.makedirs(dropbox, mode=0o700, exist_ok=True)
        try:
            subprocess.run(
                ["chown", "player:player", dropbox],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        setup_turn_gpg(ctx, power)
        try:
            prompt = turn_context(
                power, state, game_dir, dropbox=dropbox,
            )
            run_agent(
                ctx, power, prompt,
                env_extra={"PERFID_DROPBOX": dropbox},
            )
            collect_from_dropbox(
                game_dir, power, year, phase, dropbox,
            )
        finally:
            cleanup_turn_gpg(ctx, power)

        _save_checkpoint(game_dir, year, phase, 1, active, i + 1)

    _clear_checkpoint(game_dir)

    # Apply adjustments and advance
    apply_adjustments_and_advance(ctx, state)


# --- Adjudication ---


def adjudicate_movement(ctx, state):
    """Collect orders, run jDip adjudication, update state.

    Args:
        ctx: Game context dict.
        state: Current game state dict.
    """
    game_dir = ctx["game_dir"]
    gm_gnupghome = ctx["gm_gnupghome"]
    logger = ctx["logger"]
    year = state["year"]
    phase = state["phase"]

    print("  Adjudicating via jDip...")

    # Reload state (agents may have changed it — though they shouldn't)
    state = load_state(game_dir)

    # Snapshot SC ownership before adjudication for change detection
    sc_before = dict(state.get("sc_ownership", {}))

    # Collect and decrypt orders
    collected = collect_orders(
        game_dir, year, phase, gm_gnupghome, state
    )
    all_orders = collected["orders"]

    if collected["defaults"]:
        print(f"  Default orders for: "
              f"{', '.join(collected['defaults'])}")
    if collected["errors"]:
        for power, errs in collected["errors"].items():
            for order, msg in errs:
                print(f"  {power} order error: {order} — {msg}",
                      file=sys.stderr)

    # Print submitted orders
    for power in sorted(all_orders):
        orders = all_orders[power]
        print(f"  {power} orders:")
        for o in orders:
            print(f"    {o}")

    # Log submitted orders
    for power, orders in all_orders.items():
        logger.orders_submitted(power, orders, phase)

    # Adjudicate (jDip is in the container)
    state = adjudicate(state, all_orders, game_dir)

    order_results = state.pop("order_results", [])
    logger.adjudication_result(phase, order_results)

    # Print per-order results
    if order_results:
        print("  Order results:")
        for r in order_results:
            status = r.get("result", "unknown")
            msg = r.get("message", "")
            detail = f" ({msg})" if msg else ""
            print(f"    {r.get('order', '?')}: {status}{detail}")

    # Print dislodged units
    dislodged = state.get("dislodged", [])
    if dislodged:
        print("  Dislodged units:")
        for d in dislodged:
            retreats = ", ".join(d.get("retreats", []))
            print(f"    {d['power']} {d['unit']['type']} "
                  f"{d['unit']['location']}"
                  f" (can retreat to: {retreats or 'none — must disband'})")

    # Print SC ownership changes
    sc_after = state.get("sc_ownership", {})
    sc_changes = []
    for sc in sorted(set(list(sc_before) + list(sc_after))):
        old = sc_before.get(sc)
        new = sc_after.get(sc)
        if old != new:
            if old:
                sc_changes.append(f"    {sc}: {old} -> {new}")
            else:
                sc_changes.append(f"    {sc}: neutral -> {new}")
    if sc_changes:
        print("  SC ownership changes:")
        for c in sc_changes:
            print(c)

    _log(logger, "adjudication", year=year, phase=phase,
         order_results=order_results,
         dislodged=dislodged,
         resolved_units=state["units"],
         sc_changes={sc: {"from": sc_before.get(sc),
                          "to": sc_after.get(sc)}
                     for sc in set(list(sc_before) + list(sc_after))
                     if sc_before.get(sc) != sc_after.get(sc)})

    print(f"  Phase advanced to: {state['year']} {state['phase']}")


def apply_retreats_and_advance(ctx, state):
    """Apply retreat orders and advance to next phase.

    Args:
        ctx: Game context dict.
        state: Current game state dict.
    """
    game_dir = ctx["game_dir"]
    gm_gnupghome = ctx["gm_gnupghome"]
    logger = ctx["logger"]

    # Reload state
    state = load_state(game_dir)
    year = state["year"]
    phase = state["phase"]

    collected = collect_orders(
        game_dir, year, phase, gm_gnupghome, state
    )

    # Build retreat order structures from collected orders
    dislodged = state.get("dislodged", [])
    retreat_orders = []
    for d in dislodged:
        power = d["power"]
        power_orders = collected["orders"].get(power, [])
        applied = False
        for o in power_orders:
            parts = o.split()
            if len(parts) >= 4 and parts[2] == "-":
                retreat_orders.append({
                    "power": power,
                    "unit": d["unit"],
                    "action": "retreat",
                    "destination": " ".join(parts[3:]),
                })
                applied = True
                break
        if not applied:
            retreat_orders.append({
                "power": power,
                "unit": d["unit"],
                "action": "disband",
            })

    # Print retreat actions
    if retreat_orders:
        print("  Retreat results:")
        for r in retreat_orders:
            unit = r["unit"]
            if r["action"] == "retreat":
                print(f"    {r['power']} {unit['type']} "
                      f"{unit['location']} -> {r['destination']}")
            else:
                print(f"    {r['power']} {unit['type']} "
                      f"{unit['location']} disbanded")

    state = apply_retreats(state, retreat_orders, game_dir)
    _log(logger, "retreats_applied", year=year, phase=phase,
         retreat_orders=retreat_orders,
         units=state["units"])
    state = next_phase(state)
    save_state(state, game_dir)
    print(f"  Phase advanced to: {state['year']} {state['phase']}")


def apply_adjustments_and_advance(ctx, state):
    """Apply winter adjustment orders and advance to next phase.

    Args:
        ctx: Game context dict.
        state: Current game state dict.
    """
    game_dir = ctx["game_dir"]
    gm_gnupghome = ctx["gm_gnupghome"]
    logger = ctx["logger"]

    # Reload state
    state = load_state(game_dir)
    year = state["year"]
    phase = state["phase"]

    adj_counts = adjustment_counts(state)
    collected = collect_orders(
        game_dir, year, phase, gm_gnupghome, state
    )

    adjustments = {}
    for power, count in adj_counts.items():
        if count == 0:
            continue
        power_orders = collected["orders"].get(power, [])
        actions = []
        for o in power_orders:
            parts = o.split()
            if "Build" in o or "build" in o:
                unit_type = (
                    "Army" if parts[1].upper() in ("A", "ARMY")
                    else "Fleet"
                )
                location = " ".join(parts[2:])
                actions.append({
                    "action": "build",
                    "unit": {"type": unit_type, "location": location},
                })
            elif any(
                kw in o
                for kw in ("Disband", "disband", "Remove", "remove")
            ):
                unit_type = (
                    "Army" if parts[1].upper() in ("A", "ARMY")
                    else "Fleet"
                )
                location = " ".join(parts[2:])
                actions.append({
                    "action": "disband",
                    "unit": {"type": unit_type, "location": location},
                })
        if actions:
            adjustments[power] = actions

    # Print adjustment summary
    if adj_counts:
        print("  Adjustment counts:")
        for power in sorted(adj_counts):
            count = adj_counts[power]
            if count > 0:
                print(f"    {power}: +{count} build(s)")
            elif count < 0:
                print(f"    {power}: {count} disband(s)")
    if adjustments:
        print("  Adjustments applied:")
        for power in sorted(adjustments):
            for a in adjustments[power]:
                unit = a["unit"]
                print(f"    {power} {a['action']} "
                      f"{unit['type']} {unit['location']}")

    state = apply_adjustments(state, adjustments, game_dir)
    _log(logger, "adjustments_applied", year=year, phase=phase,
         adjustment_counts=adj_counts,
         adjustments=adjustments,
         units=state["units"])
    state = next_phase(state)
    save_state(state, game_dir)

    print(format_status(state))
    print(f"  Next year: {state['year']} {state['phase']}")


# --- Main entry point ---


def run(game_id, games_dir, script_dir):
    """Main game loop.

    Args:
        game_id: Unique game identifier.
        games_dir: Base directory for all games.
        script_dir: Directory containing perfid Python modules.
    """
    game_dir = os.path.join(games_dir, game_id)
    gm_gnupghome = os.path.join(game_dir, "gm-gpg")

    if not os.path.exists(os.path.join(game_dir, "state.json")):
        raise PerfidError(f"Game '{game_id}' not found at {game_dir}")

    # Pre-flight: verify keys exist
    for power in POWERS:
        key_file = os.path.join(game_dir, "keys", f"{power}.key.gpg")
        if not os.path.exists(key_file):
            raise PerfidError(
                f"Missing key for {power}. "
                f"Run 'perfid new {game_id}' first."
            )

    logger = GameLogger(game_dir)

    ctx = {
        "game_id": game_id,
        "game_dir": game_dir,
        "gm_gnupghome": gm_gnupghome,
        "script_dir": script_dir,
        "logger": logger,
    }

    print(f"Starting game loop for '{game_id}'...")

    while True:
        # Route any undelivered messages from a previous interrupted run
        for power in POWERS:
            route_messages(game_dir, power)

        state = load_state(game_dir)

        # Check for winner
        if state["winner"]:
            print()
            print("==========================================")
            print(f"  GAME OVER — {state['winner']} wins!")
            print("==========================================")
            print(format_status(state))
            logger.game_ended(
                winner=state["winner"], reason="solo victory"
            )
            break

        phase = Phase(state["phase"])
        year = state["year"]

        print()
        print(f"--- {year} {phase.value} ---")

        # Log phase start
        season = phase.value.split()[0]
        phase_type = (
            " ".join(phase.value.split()[1:])
            if " " in phase.value
            else "Turn"
        )
        logger.phase_start(year, season, phase_type)

        if phase in (Phase.SPRING, Phase.FALL):
            _movement_phase(ctx, state)
        elif phase in (Phase.SPRING_RETREAT, Phase.FALL_RETREAT):
            _retreat_phase(ctx, state)
        elif phase == Phase.WINTER_ADJUSTMENT:
            _winter_phase(ctx, state)
        else:
            raise PerfidError(f"Unexpected phase: {phase.value}")

        # Archive messages after each phase
        try:
            archive_phase(
                game_dir, year,
                phase.value.replace(" ", "_"),
            )
        except Exception:
            pass  # non-fatal
