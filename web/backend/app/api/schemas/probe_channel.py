"""What a probe exchanges with the platform over its return channel, beyond
the sensor log records (ADR 0013)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.api.schemas.common import ApiModel

# A PEM CSR for an EC or RSA key is well under 4 KB; the limit only keeps an
# authenticated but broken probe from handing the parser megabytes.
MAX_CSR_LENGTH = 16_384


class CertificateRenewalIn(ApiModel):
    csr: str = Field(min_length=1, max_length=MAX_CSR_LENGTH)


class CertificateRenewalOut(ApiModel):
    certificate: str
    not_after: datetime
