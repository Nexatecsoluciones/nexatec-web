import enum


class Currency(str, enum.Enum):
    """Solo PYG confirmado en la documentacion oficial de Bancard (vPOS
    Compra Simple 0.3.1: 'currency String(3) - PYG (Gs)'). USD aparece en
    SDKs de terceros pero no en la doc oficial -- se deja el valor listo
    para cuando/si se confirme con Bancard para este comercio especifico,
    ver docs/PAYMENTS.md."""

    PYG = "PYG"


class PaymentMethod(str, enum.Enum):
    BANK_TRANSFER = "BANK_TRANSFER"
    BANCARD_CARD = "BANCARD_CARD"


class PaymentOrderStatus(str, enum.Enum):
    # Comunes
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    # Especificos de transferencia bancaria
    PENDING_TRANSFER = "PENDING_TRANSFER"
    PROOF_UPLOADED = "PROOF_UPLOADED"
    UNDER_REVIEW = "UNDER_REVIEW"
    # Especificos de Bancard (tarjeta)
    AWAITING_CARD_CONFIRMATION = "AWAITING_CARD_CONFIRMATION"


class SubscriptionStatus(str, enum.Enum):
    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"


class BillingPeriod(str, enum.Enum):
    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"
