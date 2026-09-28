from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

# Parametros por encima del default de argon2-cffi: mas costo de memoria/tiempo
# para dificultar ataques de fuerza bruta offline si se filtran los hashes.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

MIN_PASSWORD_LENGTH = 12


class WeakPasswordError(ValueError):
    pass


def validate_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"La contrasena debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
        )
    has_letter = any(c.isalpha() for c in password)
    has_digit = any(c.isdigit() for c in password)
    if not (has_letter and has_digit):
        raise WeakPasswordError("La contrasena debe combinar letras y numeros.")


def hash_password(password: str) -> str:
    validate_password_policy(password)
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
