"""GPG helpers for perfid agent isolation.

Wraps gpg commands for key generation, encryption, decryption, and key
import/trust. Each function operates on a specified GNUPGHOME so that
GM and agent keyrings stay separate.
"""

import os
import shutil
import subprocess
import tempfile


# 7 Diplomacy powers
POWERS = ["Austria", "England", "France", "Germany", "Italy", "Russia", "Turkey"]

# Key parameters
KEY_TYPE = "RSA"
KEY_LENGTH = "2048"
KEY_EXPIRE = "0"  # no expiry for game keys


def _run_gpg(args, gnupghome, input_data=None):
    """Run a gpg command with the given GNUPGHOME.

    Returns CompletedProcess. Raises subprocess.CalledProcessError on
    failure.
    """
    env = os.environ.copy()
    env["GNUPGHOME"] = gnupghome
    cmd = [
        "gpg", "--batch", "--yes", "--no-tty",
        "--homedir", gnupghome,
    ] + args
    return subprocess.run(
        cmd,
        input=input_data,
        capture_output=True,
        check=True,
        env=env,
    )


def init_gnupghome(gnupghome):
    """Create and permission a GNUPGHOME directory."""
    os.makedirs(gnupghome, mode=0o700, exist_ok=True)


def generate_key(gnupghome, name, email=None):
    """Generate a GPG key pair in the given keyring.

    Args:
        gnupghome: Path to the GPG home directory.
        name: Real name for the key (e.g. "GM" or "England").
        email: Email for the key. Defaults to <name>@perfid.local.

    Returns:
        The fingerprint of the generated key.
    """
    if email is None:
        email = f"{name.lower()}@perfid.local"

    init_gnupghome(gnupghome)

    batch_commands = (
        f"Key-Type: {KEY_TYPE}\n"
        f"Key-Length: {KEY_LENGTH}\n"
        f"Name-Real: {name}\n"
        f"Name-Email: {email}\n"
        f"Expire-Date: {KEY_EXPIRE}\n"
        "%no-protection\n"
        "%commit\n"
    )
    _run_gpg(
        ["--gen-key"],
        gnupghome,
        input_data=batch_commands.encode(),
    )

    return get_fingerprint(gnupghome, email)


def get_fingerprint(gnupghome, identifier):
    """Get the fingerprint for a key matching identifier (email or name).

    Returns the fingerprint string, or None if not found.
    """
    try:
        result = _run_gpg(
            ["--with-colons", "--fingerprint", identifier],
            gnupghome,
        )
    except subprocess.CalledProcessError:
        return None
    for line in result.stdout.decode().splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    return None


def export_public_key(gnupghome, identifier):
    """Export the public key for identifier as ASCII-armored text."""
    result = _run_gpg(
        ["--armor", "--export", identifier],
        gnupghome,
    )
    return result.stdout.decode()


def import_key(gnupghome, key_data):
    """Import an ASCII-armored public key into the keyring.

    Returns the fingerprint of the imported key.
    """
    init_gnupghome(gnupghome)
    result = _run_gpg(
        ["--import", "--import-options", "import-show",
         "--with-colons"],
        gnupghome,
        input_data=key_data.encode() if isinstance(key_data, str) else key_data,
    )
    # Parse fingerprint from import output
    for line in result.stderr.decode().splitlines() + result.stdout.decode().splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    return None


def trust_key(gnupghome, fingerprint, trust_level=5):
    """Set the trust level for a key (5 = ultimate trust).

    This is needed so gpg doesn't complain about untrusted keys when
    encrypting.
    """
    trust_data = f"{fingerprint}:{trust_level}:\n"
    _run_gpg(
        ["--import-ownertrust"],
        gnupghome,
        input_data=trust_data.encode(),
    )


def import_and_trust(gnupghome, key_data):
    """Import a public key and set it to ultimate trust.

    Returns the fingerprint of the imported key.
    """
    fingerprint = import_key(gnupghome, key_data)
    if fingerprint:
        trust_key(gnupghome, fingerprint)
    return fingerprint


def encrypt(gnupghome, plaintext, recipient_email):
    """Encrypt plaintext for a recipient using their public key.

    Args:
        gnupghome: Path to keyring that has the recipient's public key.
        plaintext: String to encrypt.
        recipient_email: Email of the recipient key.

    Returns:
        ASCII-armored ciphertext string.
    """
    result = _run_gpg(
        ["--armor", "--encrypt", "--trust-model", "always",
         "--recipient", recipient_email],
        gnupghome,
        input_data=plaintext.encode(),
    )
    return result.stdout.decode()


def decrypt(gnupghome, ciphertext):
    """Decrypt ASCII-armored ciphertext using the private key in gnupghome.

    Args:
        gnupghome: Path to keyring containing the private key.
        ciphertext: ASCII-armored encrypted text.

    Returns:
        Decrypted plaintext string.
    """
    result = _run_gpg(
        ["--decrypt"],
        gnupghome,
        input_data=ciphertext.encode() if isinstance(ciphertext, str) else ciphertext,
    )
    return result.stdout.decode()


def encrypt_to_file(gnupghome, plaintext, recipient_email, output_path):
    """Encrypt plaintext and write ciphertext to a file."""
    _run_gpg(
        ["--armor", "--encrypt", "--trust-model", "always",
         "--recipient", recipient_email,
         "--output", output_path],
        gnupghome,
        input_data=plaintext.encode(),
    )
    return output_path


def decrypt_file(gnupghome, input_path):
    """Decrypt a .gpg file and return the plaintext."""
    result = _run_gpg(
        ["--decrypt", input_path],
        gnupghome,
    )
    return result.stdout.decode()


def generate_player_key(game_dir, power, gm_gnupghome):
    """Generate a player key pair, encrypt private key with GM key.

    Creates a temporary keyring, generates the player's key pair,
    exports the private key encrypted with the GM's public key
    (stored in keys/{power}.key.gpg), and exports the public key
    (stored in pubkeys/{power}.asc).

    Args:
        game_dir: Path to the game directory.
        power: Diplomacy power name (e.g. "England").
        gm_gnupghome: Path to GM's GPG home (has GM private+public key).

    Returns:
        Path to the encrypted private key file.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        key_email = f"{power.lower()}@perfid.local"

        # Generate key in temp keyring
        generate_key(tmpdir, power, key_email)

        # Export private key (ASCII-armored)
        result = _run_gpg(
            ["--armor", "--export-secret-keys", key_email],
            tmpdir,
        )
        private_key = result.stdout

        # Encrypt private key with GM's public key
        keys_dir = os.path.join(game_dir, "keys")
        os.makedirs(keys_dir, exist_ok=True)
        encrypted_path = os.path.join(keys_dir, f"{power}.key.gpg")
        _run_gpg(
            ["--armor", "--encrypt", "--trust-model", "always",
             "--recipient", "gm@perfid.local",
             "--output", encrypted_path],
            gm_gnupghome,
            input_data=private_key,
        )

        # Export public key
        pub_key = export_public_key(tmpdir, key_email)
        pubkeys_dir = os.path.join(game_dir, "pubkeys")
        os.makedirs(pubkeys_dir, exist_ok=True)
        pubkey_path = os.path.join(pubkeys_dir, f"{power}.asc")
        with open(pubkey_path, "w") as f:
            f.write(pub_key)

        return encrypted_path
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def import_all_pubkeys(gnupghome, game_dir):
    """Import and trust all public keys from game_dir/pubkeys/.

    Used by agents to encrypt messages to GM and other agents.

    Returns:
        Dict mapping filename (without .asc) to fingerprint.
    """
    pubkeys_dir = os.path.join(game_dir, "pubkeys")
    imported = {}
    if not os.path.isdir(pubkeys_dir):
        return imported
    for fname in sorted(os.listdir(pubkeys_dir)):
        if not fname.endswith(".asc"):
            continue
        path = os.path.join(pubkeys_dir, fname)
        with open(path) as f:
            key_data = f.read()
        fingerprint = import_and_trust(gnupghome, key_data)
        name = fname.removesuffix(".asc")
        imported[name] = fingerprint
    return imported
