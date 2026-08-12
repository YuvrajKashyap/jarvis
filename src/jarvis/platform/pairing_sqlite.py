import json
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import Field as SqlField
from sqlmodel import Session, SQLModel, func, select

from jarvis.platform.pairing import (
    PairedDevice,
    StoredChallenge,
    StoredOffer,
    StoredSession,
)
from jarvis.platform.sqlite import SQLiteStore


class PairingOfferRow(SQLModel, table=True):
    __tablename__ = "pairing_offer"

    pairing_id: str = SqlField(primary_key=True)
    secret_hash: str
    expires_at: str
    used: bool


class PairedDeviceRow(SQLModel, table=True):
    __tablename__ = "paired_device"

    device_id: str = SqlField(primary_key=True)
    public_key_jwk_json: str
    paired_at: str


class PhoneChallengeRow(SQLModel, table=True):
    __tablename__ = "phone_challenge"

    challenge_id: str = SqlField(primary_key=True)
    device_id: str = SqlField(index=True)
    challenge: bytes
    expires_at: str
    used: bool


class PhoneSessionRow(SQLModel, table=True):
    __tablename__ = "phone_session"

    token_hash: str = SqlField(primary_key=True)
    device_id: str = SqlField(index=True)
    expires_at: str


class SQLitePairingStore:
    def __init__(self, store: SQLiteStore) -> None:
        self._store = store

    def put_offer(self, offer: StoredOffer) -> None:
        with self._store._write_lock, Session(self._store.engine) as session:
            session.merge(_offer_to_row(offer))
            session.commit()

    def get_offer(self, pairing_id: UUID) -> StoredOffer | None:
        with Session(self._store.engine) as session:
            row = session.get(PairingOfferRow, str(pairing_id))
            return None if row is None else _offer_from_row(row)

    def use_offer(self, offer: StoredOffer) -> bool:
        with self._store._write_lock, Session(self._store.engine) as session:
            row = session.get(PairingOfferRow, str(offer.pairing_id))
            if row is None or row.used or _offer_from_row(row) != offer:
                return False
            row.used = True
            session.add(row)
            session.commit()
            return True

    def put_device(self, device: PairedDevice) -> None:
        with self._store._write_lock, Session(self._store.engine) as session:
            session.merge(
                PairedDeviceRow(
                    device_id=device.device_id,
                    public_key_jwk_json=json.dumps(
                        device.public_key_jwk,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    paired_at=_dump_datetime(device.paired_at),
                )
            )
            session.commit()

    def get_device(self, device_id: str) -> PairedDevice | None:
        with Session(self._store.engine) as session:
            row = session.get(PairedDeviceRow, device_id)
            if row is None:
                return None
            return PairedDevice(
                device_id=row.device_id,
                public_key_jwk=json.loads(row.public_key_jwk_json),
                paired_at=_load_datetime(row.paired_at),
            )

    def paired_device_count(self) -> int:
        with Session(self._store.engine) as session:
            return int(session.exec(select(func.count()).select_from(PairedDeviceRow)).one())

    def put_challenge(self, challenge: StoredChallenge) -> None:
        with self._store._write_lock, Session(self._store.engine) as session:
            session.merge(_challenge_to_row(challenge))
            session.commit()

    def get_challenge(self, challenge_id: UUID) -> StoredChallenge | None:
        with Session(self._store.engine) as session:
            row = session.get(PhoneChallengeRow, str(challenge_id))
            return None if row is None else _challenge_from_row(row)

    def use_challenge(self, challenge: StoredChallenge) -> bool:
        with self._store._write_lock, Session(self._store.engine) as session:
            row = session.get(PhoneChallengeRow, str(challenge.challenge_id))
            if row is None or row.used or _challenge_from_row(row) != challenge:
                return False
            row.used = True
            session.add(row)
            session.commit()
            return True

    def put_session(self, session: StoredSession) -> None:
        with self._store._write_lock, Session(self._store.engine) as database_session:
            database_session.merge(
                PhoneSessionRow(
                    token_hash=session.token_hash,
                    device_id=session.device_id,
                    expires_at=_dump_datetime(session.expires_at),
                )
            )
            database_session.commit()

    def get_session(self, token_hash: str) -> StoredSession | None:
        with Session(self._store.engine) as session:
            row = session.get(PhoneSessionRow, token_hash)
            if row is None:
                return None
            return StoredSession(
                token_hash=row.token_hash,
                device_id=row.device_id,
                expires_at=_load_datetime(row.expires_at),
            )


def _offer_to_row(offer: StoredOffer) -> PairingOfferRow:
    return PairingOfferRow(
        pairing_id=str(offer.pairing_id),
        secret_hash=offer.secret_hash,
        expires_at=_dump_datetime(offer.expires_at),
        used=offer.used,
    )


def _offer_from_row(row: PairingOfferRow) -> StoredOffer:
    return StoredOffer(
        pairing_id=UUID(row.pairing_id),
        secret_hash=row.secret_hash,
        expires_at=_load_datetime(row.expires_at),
        used=row.used,
    )


def _challenge_to_row(challenge: StoredChallenge) -> PhoneChallengeRow:
    return PhoneChallengeRow(
        challenge_id=str(challenge.challenge_id),
        device_id=challenge.device_id,
        challenge=challenge.challenge,
        expires_at=_dump_datetime(challenge.expires_at),
        used=challenge.used,
    )


def _challenge_from_row(row: PhoneChallengeRow) -> StoredChallenge:
    return StoredChallenge(
        challenge_id=UUID(row.challenge_id),
        device_id=row.device_id,
        challenge=row.challenge,
        expires_at=_load_datetime(row.expires_at),
        used=row.used,
    )


def _dump_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
