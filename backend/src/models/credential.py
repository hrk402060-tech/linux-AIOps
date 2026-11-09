from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum as SqlEnum, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.models.asset import Asset


class CredentialType(str, Enum):
    PASSWORD = "password"
    PRIVATE_KEY = "private_key"


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    credential_type: Mapped[CredentialType] = mapped_column(
        SqlEnum(
            CredentialType,
            name="credential_type",
            values_callable=lambda items: [item.value for item in items],
        )
    )
    username: Mapped[str] = mapped_column(String(64))
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    key_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assets: Mapped[list["Asset"]] = relationship(back_populates="credential")

    @property
    def asset_count(self) -> int:
        return len(self.assets)
