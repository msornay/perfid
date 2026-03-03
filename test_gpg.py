"""Tests for gpg.py — GPG helper functions for perfid."""

import os

import pytest

import gpg


@pytest.fixture
def gnupghome(tmp_path):
    """Create a temporary GPG home directory."""
    home = str(tmp_path / "gnupg")
    gpg.init_gnupghome(home)
    return home


@pytest.fixture
def keyed_home(gnupghome):
    """GPG home with a generated key pair."""
    gpg.generate_key(gnupghome, "TestUser", "test@perfid.local")
    return gnupghome


class TestKeyGeneration:
    def test_generate_key_returns_fingerprint(self, gnupghome):
        fp = gpg.generate_key(gnupghome, "Alice", "alice@perfid.local")
        assert fp is not None
        assert len(fp) == 40  # SHA-1 fingerprint

    def test_generate_key_creates_keyring(self, tmp_path):
        home = str(tmp_path / "fresh")
        fp = gpg.generate_key(home, "Bob", "bob@perfid.local")
        assert fp is not None
        assert os.path.isdir(home)

    def test_get_fingerprint(self, keyed_home):
        fp = gpg.get_fingerprint(keyed_home, "test@perfid.local")
        assert fp is not None
        assert len(fp) == 40

    def test_get_fingerprint_unknown_returns_none(self, gnupghome):
        fp = gpg.get_fingerprint(gnupghome, "nobody@perfid.local")
        assert fp is None

    def test_default_email(self, gnupghome):
        gpg.generate_key(gnupghome, "France")
        fp = gpg.get_fingerprint(gnupghome, "france@perfid.local")
        assert fp is not None


class TestExportImport:
    def test_export_public_key(self, keyed_home):
        armor = gpg.export_public_key(keyed_home, "test@perfid.local")
        assert "-----BEGIN PGP PUBLIC KEY BLOCK-----" in armor
        assert "-----END PGP PUBLIC KEY BLOCK-----" in armor

    def test_import_key(self, keyed_home, tmp_path):
        armor = gpg.export_public_key(keyed_home, "test@perfid.local")
        other_home = str(tmp_path / "other")
        gpg.init_gnupghome(other_home)
        fp = gpg.import_key(other_home, armor)
        assert fp is not None
        assert len(fp) == 40

    def test_import_and_trust(self, keyed_home, tmp_path):
        armor = gpg.export_public_key(keyed_home, "test@perfid.local")
        other_home = str(tmp_path / "other")
        gpg.init_gnupghome(other_home)
        fp = gpg.import_and_trust(other_home, armor)
        assert fp is not None


class TestEncryptDecrypt:
    def test_encrypt_decrypt_roundtrip(self, keyed_home):
        plaintext = "Attack Munich in Spring 1901"
        ciphertext = gpg.encrypt(
            keyed_home, plaintext, "test@perfid.local"
        )
        assert "-----BEGIN PGP MESSAGE-----" in ciphertext
        assert plaintext not in ciphertext
        decrypted = gpg.decrypt(keyed_home, ciphertext)
        assert decrypted == plaintext

    def test_encrypt_decrypt_unicode(self, keyed_home):
        plaintext = "Déployez les troupes à München"
        ciphertext = gpg.encrypt(
            keyed_home, plaintext, "test@perfid.local"
        )
        decrypted = gpg.decrypt(keyed_home, ciphertext)
        assert decrypted == plaintext

    def test_encrypt_decrypt_multiline(self, keyed_home):
        plaintext = "Line 1\nLine 2\nLine 3\n"
        ciphertext = gpg.encrypt(
            keyed_home, plaintext, "test@perfid.local"
        )
        decrypted = gpg.decrypt(keyed_home, ciphertext)
        assert decrypted == plaintext

    def test_cross_keyring_encrypt_decrypt(self, tmp_path):
        """Sender encrypts with recipient's public key, recipient decrypts."""
        sender_home = str(tmp_path / "sender")
        recipient_home = str(tmp_path / "recipient")

        # Recipient generates keys
        gpg.generate_key(recipient_home, "England", "england@perfid.local")
        pub_key = gpg.export_public_key(recipient_home, "england@perfid.local")

        # Sender imports recipient's public key
        gpg.init_gnupghome(sender_home)
        gpg.import_and_trust(sender_home, pub_key)

        # Sender encrypts
        plaintext = "I propose an alliance against France"
        ciphertext = gpg.encrypt(
            sender_home, plaintext, "england@perfid.local"
        )

        # Recipient decrypts
        decrypted = gpg.decrypt(recipient_home, ciphertext)
        assert decrypted == plaintext

    def test_wrong_key_cannot_decrypt(self, tmp_path):
        """A third party cannot decrypt a message meant for someone else."""
        alice_home = str(tmp_path / "alice")
        bob_home = str(tmp_path / "bob")
        eve_home = str(tmp_path / "eve")

        gpg.generate_key(alice_home, "Alice", "alice@perfid.local")
        gpg.generate_key(bob_home, "Bob", "bob@perfid.local")
        gpg.generate_key(eve_home, "Eve", "eve@perfid.local")

        # Alice gets Bob's public key and encrypts for Bob
        bob_pub = gpg.export_public_key(bob_home, "bob@perfid.local")
        gpg.import_and_trust(alice_home, bob_pub)
        ciphertext = gpg.encrypt(
            alice_home, "Secret for Bob only", "bob@perfid.local"
        )

        # Eve tries to decrypt — should fail
        with pytest.raises(Exception):
            gpg.decrypt(eve_home, ciphertext)


class TestFileEncryption:
    def test_encrypt_to_file_and_decrypt(self, keyed_home, tmp_path):
        plaintext = "Orders: A Vie - Bud"
        outpath = str(tmp_path / "orders.gpg")
        gpg.encrypt_to_file(
            keyed_home, plaintext, "test@perfid.local", outpath
        )
        assert os.path.exists(outpath)
        decrypted = gpg.decrypt_file(keyed_home, outpath)
        assert decrypted == plaintext


class TestGeneratePlayerKey:
    def test_creates_encrypted_key(self, tmp_path):
        game_dir = str(tmp_path / "game")
        gm_home = str(tmp_path / "gm")
        gpg.generate_key(gm_home, "GM", "gm@perfid.local")
        result = gpg.generate_player_key(game_dir, "France", gm_home)
        assert os.path.exists(result)
        assert result.endswith(".key.gpg")

    def test_creates_public_key(self, tmp_path):
        game_dir = str(tmp_path / "game")
        gm_home = str(tmp_path / "gm")
        gpg.generate_key(gm_home, "GM", "gm@perfid.local")
        gpg.generate_player_key(game_dir, "England", gm_home)
        pubkey = os.path.join(game_dir, "pubkeys", "England.asc")
        assert os.path.exists(pubkey)
        with open(pubkey) as f:
            assert "BEGIN PGP PUBLIC KEY BLOCK" in f.read()

    def test_encrypted_key_decryptable(self, tmp_path):
        game_dir = str(tmp_path / "game")
        gm_home = str(tmp_path / "gm")
        gpg.generate_key(gm_home, "GM", "gm@perfid.local")
        enc_path = gpg.generate_player_key(
            game_dir, "Germany", gm_home
        )
        # GM can decrypt the key
        decrypted = gpg.decrypt_file(gm_home, enc_path)
        assert "PRIVATE KEY" in decrypted

    def test_decrypted_key_importable(self, tmp_path):
        game_dir = str(tmp_path / "game")
        gm_home = str(tmp_path / "gm")
        gpg.generate_key(gm_home, "GM", "gm@perfid.local")
        enc_path = gpg.generate_player_key(
            game_dir, "Italy", gm_home
        )
        # Decrypt and import into a fresh keyring
        decrypted = gpg.decrypt_file(gm_home, enc_path)
        fresh = str(tmp_path / "fresh")
        gpg.init_gnupghome(fresh)
        fp = gpg.import_key(fresh, decrypted)
        assert fp is not None
        assert len(fp) == 40

    def test_all_powers(self, tmp_path):
        game_dir = str(tmp_path / "game")
        gm_home = str(tmp_path / "gm")
        gpg.generate_key(gm_home, "GM", "gm@perfid.local")
        for power in gpg.POWERS:
            gpg.generate_player_key(game_dir, power, gm_home)
        keys_dir = os.path.join(game_dir, "keys")
        pubkeys_dir = os.path.join(game_dir, "pubkeys")
        for power in gpg.POWERS:
            assert os.path.exists(
                os.path.join(keys_dir, f"{power}.key.gpg")
            )
            assert os.path.exists(
                os.path.join(pubkeys_dir, f"{power}.asc")
            )


class TestImportAllPubkeys:
    def test_import_all_pubkeys(self, tmp_path):
        game_dir = str(tmp_path / "game-001")
        importer_home = str(tmp_path / "importer")

        # Generate GM key and publish
        gm_home = str(tmp_path / "gm")
        gpg.generate_key(gm_home, "GM", "gm@perfid.local")
        pubkeys_dir = os.path.join(game_dir, "pubkeys")
        os.makedirs(pubkeys_dir, exist_ok=True)
        gm_pub = gpg.export_public_key(gm_home, "gm@perfid.local")
        with open(os.path.join(pubkeys_dir, "GM.asc"), "w") as f:
            f.write(gm_pub)

        # Generate two agent keys and publish
        eng_home = str(tmp_path / "eng")
        gpg.generate_key(eng_home, "England", "england@perfid.local")
        eng_pub = gpg.export_public_key(eng_home, "england@perfid.local")
        with open(os.path.join(pubkeys_dir, "England.asc"), "w") as f:
            f.write(eng_pub)

        fra_home = str(tmp_path / "fra")
        gpg.generate_key(fra_home, "France", "france@perfid.local")
        fra_pub = gpg.export_public_key(fra_home, "france@perfid.local")
        with open(os.path.join(pubkeys_dir, "France.asc"), "w") as f:
            f.write(fra_pub)

        # Import all into a fresh keyring
        gpg.init_gnupghome(importer_home)
        imported = gpg.import_all_pubkeys(importer_home, game_dir)

        assert "GM" in imported
        assert "England" in imported
        assert "France" in imported
        assert len(imported) == 3

    def test_full_agent_communication_flow(self, tmp_path):
        """End-to-end: GM + 2 agents, key exchange, encrypted messaging."""
        game_dir = str(tmp_path / "game-full")
        pubkeys_dir = os.path.join(game_dir, "pubkeys")
        os.makedirs(pubkeys_dir, exist_ok=True)

        # 1. GM setup
        gm_home = str(tmp_path / "gm")
        gpg.generate_key(gm_home, "GM", "gm@perfid.local")
        gm_pub = gpg.export_public_key(gm_home, "gm@perfid.local")
        with open(os.path.join(pubkeys_dir, "GM.asc"), "w") as f:
            f.write(gm_pub)

        # 2. Agent key generation + publishing
        eng_home = str(tmp_path / "eng")
        gpg.generate_key(eng_home, "England", "england@perfid.local")
        eng_pub = gpg.export_public_key(eng_home, "england@perfid.local")
        with open(os.path.join(pubkeys_dir, "England.asc"), "w") as f:
            f.write(eng_pub)

        fra_home = str(tmp_path / "fra")
        gpg.generate_key(fra_home, "France", "france@perfid.local")
        fra_pub = gpg.export_public_key(fra_home, "france@perfid.local")
        with open(os.path.join(pubkeys_dir, "France.asc"), "w") as f:
            f.write(fra_pub)

        # 3. Each agent imports all pubkeys
        gpg.import_all_pubkeys(eng_home, game_dir)
        gpg.import_all_pubkeys(fra_home, game_dir)
        gpg.import_all_pubkeys(gm_home, game_dir)

        # 4. England sends private message to France
        msg = "Let's ally against Germany"
        ct = gpg.encrypt(eng_home, msg, "france@perfid.local")
        assert gpg.decrypt(fra_home, ct) == msg

        # 5. France sends orders to GM
        orders = "A Par - Bur\nA Mar - Spa"
        ct_orders = gpg.encrypt(fra_home, orders, "gm@perfid.local")
        assert gpg.decrypt(gm_home, ct_orders) == orders

        # 6. England cannot read France's orders to GM
        with pytest.raises(Exception):
            gpg.decrypt(eng_home, ct_orders)
