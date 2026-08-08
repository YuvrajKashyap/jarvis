import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from jarvis.platform.pairing import (
    AuthenticationError,
    InMemoryPairingStore,
    PairingError,
    PhonePairing,
    public_key_to_jwk,
)
from jarvis.platform.pairing_sqlite import SQLitePairingStore
from jarvis.platform.sqlite import SQLiteStore

NOW = datetime(2026, 8, 7, 18, 30, tzinfo=UTC)


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def test_pairing_secret_is_one_use_and_expires_after_five_minutes() -> None:
    manager = PhonePairing(InMemoryPairingStore())
    private_key = ec.generate_private_key(ec.SECP256R1())
    offer = manager.create_offer(now=NOW)

    device = manager.complete_pairing(
        pairing_id=offer.pairing_id,
        secret=offer.secret,
        device_id="yuvraj-iphone",
        public_key_jwk=public_key_to_jwk(private_key.public_key()),
        now=NOW + timedelta(minutes=4, seconds=59),
    )

    assert device.device_id == "yuvraj-iphone"
    with pytest.raises(PairingError, match="already used"):
        manager.complete_pairing(
            pairing_id=offer.pairing_id,
            secret=offer.secret,
            device_id="second-device",
            public_key_jwk=public_key_to_jwk(private_key.public_key()),
            now=NOW + timedelta(minutes=4, seconds=59),
        )

    expired = manager.create_offer(now=NOW)
    with pytest.raises(PairingError, match="expired"):
        manager.complete_pairing(
            pairing_id=expired.pairing_id,
            secret=expired.secret,
            device_id="late-device",
            public_key_jwk=public_key_to_jwk(private_key.public_key()),
            now=NOW + timedelta(minutes=5, seconds=1),
        )


def test_signed_challenge_mints_a_short_lived_session_and_blocks_replay() -> None:
    manager = PhonePairing(InMemoryPairingStore())
    private_key = ec.generate_private_key(ec.SECP256R1())
    offer = manager.create_offer(now=NOW)
    manager.complete_pairing(
        pairing_id=offer.pairing_id,
        secret=offer.secret,
        device_id="yuvraj-iphone",
        public_key_jwk=public_key_to_jwk(private_key.public_key()),
        now=NOW,
    )
    challenge = manager.create_challenge(device_id="yuvraj-iphone", now=NOW)
    signature = private_key.sign(challenge.challenge, ec.ECDSA(challenge.hash_algorithm))

    session = manager.verify_challenge(
        challenge_id=challenge.challenge_id,
        signature=b64url(signature),
        now=NOW,
    )

    assert manager.authenticate_session(session.token, now=NOW) == "yuvraj-iphone"
    assert (
        manager.authenticate_session(
            session.token,
            now=NOW + timedelta(minutes=14, seconds=59),
        )
        == "yuvraj-iphone"
    )
    with pytest.raises(AuthenticationError, match="expired"):
        manager.authenticate_session(session.token, now=NOW + timedelta(minutes=15, seconds=1))
    with pytest.raises(AuthenticationError, match="already used"):
        manager.verify_challenge(
            challenge_id=challenge.challenge_id,
            signature=b64url(signature),
            now=NOW,
        )


def test_browser_p1363_signature_is_accepted() -> None:
    manager = PhonePairing(InMemoryPairingStore())
    private_key = ec.generate_private_key(ec.SECP256R1())
    offer = manager.create_offer(now=NOW)
    manager.complete_pairing(
        pairing_id=offer.pairing_id,
        secret=offer.secret,
        device_id="yuvraj-iphone",
        public_key_jwk=public_key_to_jwk(private_key.public_key()),
        now=NOW,
    )
    challenge = manager.create_challenge(device_id="yuvraj-iphone", now=NOW)
    der = private_key.sign(challenge.challenge, ec.ECDSA(challenge.hash_algorithm))
    r, s = decode_dss_signature(der)
    browser_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    session = manager.verify_challenge(
        challenge_id=challenge.challenge_id,
        signature=b64url(browser_signature),
        now=NOW,
    )

    assert manager.authenticate_session(session.token, now=NOW) == "yuvraj-iphone"


def test_forged_signature_and_unknown_session_are_denied() -> None:
    manager = PhonePairing(InMemoryPairingStore())
    enrolled_key = ec.generate_private_key(ec.SECP256R1())
    attacker_key = ec.generate_private_key(ec.SECP256R1())
    offer = manager.create_offer(now=NOW)
    manager.complete_pairing(
        pairing_id=offer.pairing_id,
        secret=offer.secret,
        device_id="yuvraj-iphone",
        public_key_jwk=public_key_to_jwk(enrolled_key.public_key()),
        now=NOW,
    )
    challenge = manager.create_challenge(device_id="yuvraj-iphone", now=NOW)
    forged = attacker_key.sign(challenge.challenge, ec.ECDSA(challenge.hash_algorithm))

    with pytest.raises(AuthenticationError, match="signature"):
        manager.verify_challenge(
            challenge_id=challenge.challenge_id,
            signature=b64url(forged),
            now=NOW,
        )
    with pytest.raises(AuthenticationError, match="unknown"):
        manager.authenticate_session("not-a-real-session", now=NOW)


def test_paired_phone_identity_survives_host_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "jarvis.db"
    first_database = SQLiteStore(database_path)
    first_database.initialize()
    first = PhonePairing(SQLitePairingStore(first_database))
    private_key = ec.generate_private_key(ec.SECP256R1())
    offer = first.create_offer(now=NOW)
    first.complete_pairing(
        pairing_id=offer.pairing_id,
        secret=offer.secret,
        device_id="yuvraj-iphone",
        public_key_jwk=public_key_to_jwk(private_key.public_key()),
        now=NOW,
    )

    restarted_database = SQLiteStore(database_path)
    restarted_database.initialize()
    restarted = PhonePairing(SQLitePairingStore(restarted_database))
    challenge = restarted.create_challenge(device_id="yuvraj-iphone", now=NOW)
    signature = private_key.sign(challenge.challenge, ec.ECDSA(challenge.hash_algorithm))
    session = restarted.verify_challenge(
        challenge_id=challenge.challenge_id,
        signature=b64url(signature),
        now=NOW,
    )

    assert restarted.authenticate_session(session.token, now=NOW) == "yuvraj-iphone"
