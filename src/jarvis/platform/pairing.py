import base64
import hashlib
import secrets
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

PAIRING_LIFETIME = timedelta(minutes=5)
CHALLENGE_LIFETIME = timedelta(minutes=1)
SESSION_LIFETIME = timedelta(minutes=15)


class PairingError(RuntimeError):
    pass


class AuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PairingOffer:
    pairing_id: UUID
    secret: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredOffer:
    pairing_id: UUID
    secret_hash: str
    expires_at: datetime
    used: bool = False


@dataclass(frozen=True)
class PairedDevice:
    device_id: str
    public_key_jwk: dict[str, str]
    paired_at: datetime


@dataclass(frozen=True)
class Challenge:
    challenge_id: UUID
    challenge: bytes
    hash_algorithm: hashes.HashAlgorithm
    expires_at: datetime


@dataclass(frozen=True)
class StoredChallenge:
    challenge_id: UUID
    device_id: str
    challenge: bytes
    expires_at: datetime
    used: bool = False


@dataclass(frozen=True)
class PhoneSession:
    token: str
    device_id: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredSession:
    token_hash: str
    device_id: str
    expires_at: datetime


class PairingStore(Protocol):
    def put_offer(self, offer: StoredOffer) -> None: ...

    def get_offer(self, pairing_id: UUID) -> StoredOffer | None: ...

    def use_offer(self, offer: StoredOffer) -> bool: ...

    def put_device(self, device: PairedDevice) -> None: ...

    def get_device(self, device_id: str) -> PairedDevice | None: ...

    def paired_device_count(self) -> int: ...

    def put_challenge(self, challenge: StoredChallenge) -> None: ...

    def get_challenge(self, challenge_id: UUID) -> StoredChallenge | None: ...

    def use_challenge(self, challenge: StoredChallenge) -> bool: ...

    def put_session(self, session: StoredSession) -> None: ...

    def get_session(self, token_hash: str) -> StoredSession | None: ...


class InMemoryPairingStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._offers: dict[UUID, StoredOffer] = {}
        self._devices: dict[str, PairedDevice] = {}
        self._challenges: dict[UUID, StoredChallenge] = {}
        self._sessions: dict[str, StoredSession] = {}

    def put_offer(self, offer: StoredOffer) -> None:
        with self._lock:
            self._offers[offer.pairing_id] = offer

    def get_offer(self, pairing_id: UUID) -> StoredOffer | None:
        with self._lock:
            return self._offers.get(pairing_id)

    def use_offer(self, offer: StoredOffer) -> bool:
        with self._lock:
            current = self._offers.get(offer.pairing_id)
            if current != offer or current.used:
                return False
            self._offers[offer.pairing_id] = replace(current, used=True)
            return True

    def put_device(self, device: PairedDevice) -> None:
        with self._lock:
            self._devices[device.device_id] = device

    def get_device(self, device_id: str) -> PairedDevice | None:
        with self._lock:
            return self._devices.get(device_id)

    def paired_device_count(self) -> int:
        with self._lock:
            return len(self._devices)

    def put_challenge(self, challenge: StoredChallenge) -> None:
        with self._lock:
            self._challenges[challenge.challenge_id] = challenge

    def get_challenge(self, challenge_id: UUID) -> StoredChallenge | None:
        with self._lock:
            return self._challenges.get(challenge_id)

    def use_challenge(self, challenge: StoredChallenge) -> bool:
        with self._lock:
            current = self._challenges.get(challenge.challenge_id)
            if current != challenge or current.used:
                return False
            self._challenges[challenge.challenge_id] = replace(current, used=True)
            return True

    def put_session(self, session: StoredSession) -> None:
        with self._lock:
            self._sessions[session.token_hash] = session

    def get_session(self, token_hash: str) -> StoredSession | None:
        with self._lock:
            return self._sessions.get(token_hash)


class PhonePairing:
    def __init__(self, store: PairingStore) -> None:
        self._store = store

    def paired_device_count(self) -> int:
        return self._store.paired_device_count()

    def create_offer(self, *, now: datetime) -> PairingOffer:
        _require_aware(now)
        secret = secrets.token_urlsafe(32)
        offer = PairingOffer(
            pairing_id=uuid4(),
            secret=secret,
            expires_at=now + PAIRING_LIFETIME,
        )
        self._store.put_offer(
            StoredOffer(
                pairing_id=offer.pairing_id,
                secret_hash=_hash_token(secret),
                expires_at=offer.expires_at,
            )
        )
        return offer

    def complete_pairing(
        self,
        *,
        pairing_id: UUID,
        secret: str,
        device_id: str,
        public_key_jwk: dict[str, Any],
        now: datetime,
    ) -> PairedDevice:
        _require_aware(now)
        offer = self._store.get_offer(pairing_id)
        if offer is None:
            raise PairingError("pairing offer not found")
        if offer.used:
            raise PairingError("pairing offer already used")
        if now > offer.expires_at:
            raise PairingError("pairing offer expired")
        if not secrets.compare_digest(offer.secret_hash, _hash_token(secret)):
            raise PairingError("pairing secret is invalid")
        if not device_id or len(device_id) > 128:
            raise PairingError("device ID is invalid")

        normalized_jwk = _normalize_public_jwk(public_key_jwk)
        if not self._store.use_offer(offer):
            raise PairingError("pairing offer already used")
        device = PairedDevice(
            device_id=device_id,
            public_key_jwk=normalized_jwk,
            paired_at=now,
        )
        self._store.put_device(device)
        return device

    def create_challenge(self, *, device_id: str, now: datetime) -> Challenge:
        _require_aware(now)
        if self._store.get_device(device_id) is None:
            raise AuthenticationError("paired device is unknown")
        challenge = Challenge(
            challenge_id=uuid4(),
            challenge=secrets.token_bytes(32),
            hash_algorithm=hashes.SHA256(),
            expires_at=now + CHALLENGE_LIFETIME,
        )
        self._store.put_challenge(
            StoredChallenge(
                challenge_id=challenge.challenge_id,
                device_id=device_id,
                challenge=challenge.challenge,
                expires_at=challenge.expires_at,
            )
        )
        return challenge

    def verify_challenge(
        self,
        *,
        challenge_id: UUID,
        signature: str,
        now: datetime,
    ) -> PhoneSession:
        _require_aware(now)
        challenge = self._store.get_challenge(challenge_id)
        if challenge is None:
            raise AuthenticationError("challenge is unknown")
        if challenge.used:
            raise AuthenticationError("challenge already used")
        if now > challenge.expires_at:
            raise AuthenticationError("challenge expired")
        device = self._store.get_device(challenge.device_id)
        if device is None:
            raise AuthenticationError("paired device is unknown")

        try:
            signature_bytes = _decode_base64url(signature)
            _public_key_from_jwk(device.public_key_jwk).verify(
                _normalize_signature(signature_bytes),
                challenge.challenge,
                ec.ECDSA(hashes.SHA256()),
            )
        except (InvalidSignature, ValueError) as error:
            raise AuthenticationError("challenge signature is invalid") from error

        if not self._store.use_challenge(challenge):
            raise AuthenticationError("challenge already used")
        token = secrets.token_urlsafe(32)
        session = PhoneSession(
            token=token,
            device_id=device.device_id,
            expires_at=now + SESSION_LIFETIME,
        )
        self._store.put_session(
            StoredSession(
                token_hash=_hash_token(token),
                device_id=session.device_id,
                expires_at=session.expires_at,
            )
        )
        return session

    def authenticate_session(self, token: str, *, now: datetime) -> str:
        _require_aware(now)
        session = self._store.get_session(_hash_token(token))
        if session is None:
            raise AuthenticationError("session is unknown")
        if now > session.expires_at:
            raise AuthenticationError("session expired")
        return session.device_id


def public_key_to_jwk(public_key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    if not isinstance(public_key.curve, ec.SECP256R1):
        raise ValueError("only P-256 keys are supported")
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _encode_base64url(numbers.x.to_bytes(32, "big")),
        "y": _encode_base64url(numbers.y.to_bytes(32, "big")),
    }


def _normalize_public_jwk(jwk: dict[str, Any]) -> dict[str, str]:
    if set(jwk) != {"kty", "crv", "x", "y"}:
        raise PairingError("public key JWK has unexpected fields")
    normalized = {key: str(value) for key, value in jwk.items()}
    try:
        _public_key_from_jwk(normalized)
    except ValueError as error:
        raise PairingError("public key JWK is invalid") from error
    return normalized


def _public_key_from_jwk(jwk: dict[str, str]) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ValueError("only EC P-256 public keys are supported")
    x_bytes = _decode_base64url(jwk["x"])
    y_bytes = _decode_base64url(jwk["y"])
    if len(x_bytes) != 32 or len(y_bytes) != 32:
        raise ValueError("P-256 coordinates must be 32 bytes")
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(x_bytes, "big"),
        int.from_bytes(y_bytes, "big"),
        ec.SECP256R1(),
    ).public_key()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_signature(signature: bytes) -> bytes:
    if len(signature) != 64:
        return signature
    return encode_dss_signature(
        int.from_bytes(signature[:32], "big"),
        int.from_bytes(signature[32:], "big"),
    )


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset")
