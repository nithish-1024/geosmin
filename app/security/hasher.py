import re
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError


class Argon2idSecurityService:
    """
    Argon2id password hashing service adhering to RFC 9106 & OWASP recommendations:
    - Variant: Argon2id (hybrid side-channel and GPU cracking resistance)
    - Memory Cost (m): 65536 KiB (64 MiB)
    - Time Cost (t): 3 iterations
    - Parallelism (p): 4 lanes
    - Salt Length: 16 bytes (128-bit)
    - Hash Length: 32 bytes (256-bit)
    """

    def __init__(
        self,
        time_cost: int = 3,
        memory_cost: int = 65536,
        parallelism: int = 4,
        hash_len: int = 32,
        salt_len: int = 16
    ):
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            hash_len=hash_len,
            salt_len=salt_len
        )

    def hash_password(self, password: str) -> str:
        """
        Hash plaintext password returning standard PHC formatted string:
        $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>
        """
        if not password or not isinstance(password, str):
            raise ValueError("Password must be a non-empty string")
        return self._hasher.hash(password)

    def verify_password(self, password: str, password_hash: str) -> bool:
        """
        Constant-time verification of plaintext password against Argon2id hash.
        """
        if not password or not password_hash:
            return False
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """
        Determines whether the stored hash needs to be upgraded to newer parameters.
        """
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except Exception:
            return True

    @staticmethod
    def validate_password_complexity(password: str) -> tuple[bool, str]:
        """
        Enforce enterprise clinical password complexity policy:
        - Minimum 8 characters
        - At least 1 lowercase letter
        - At least 1 uppercase letter
        - At least 1 digit
        - At least 1 special character
        """
        if len(password) < 8:
            return False, "Password must be at least 8 characters long."
        if not re.search(r"[a-z]", password):
            return False, "Password must contain at least one lowercase letter."
        if not re.search(r"[A-Z]", password):
            return False, "Password must contain at least one uppercase letter."
        if not re.search(r"\d", password):
            return False, "Password must contain at least one numerical digit."
        if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
            return False, "Password must contain at least one special character (!@#$%^&* etc.)."
        return True, ""


# Singleton instance
argon2_hasher = Argon2idSecurityService()
