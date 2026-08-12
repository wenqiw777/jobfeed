"""Privacy-preserving benchmark machine identity helpers."""

from __future__ import annotations

import hashlib


def machine_fingerprint(machine_token: str, cpu_identifier: str) -> str:
    """Hash an explicit machine token and CPU without exposing plaintext.

    Args:
        machine_token: Explicit shared token for one benchmark host.
        cpu_identifier: Stable CPU model identifier.

    Returns:
        Combined lowercase SHA-256 digest.
    """
    return hashlib.sha256(f"{machine_token}\0{cpu_identifier}".encode()).hexdigest()


def component_fingerprints(
    machine_token: str, cpu_identifier: str
) -> tuple[str, str, str]:
    """Return combined, token-only, and CPU-only hashed fingerprints.

    Args:
        machine_token: Explicit shared token for one benchmark host.
        cpu_identifier: Stable CPU model identifier.

    Returns:
        Combined, machine-token, and CPU SHA-256 values.
    """
    return (
        machine_fingerprint(machine_token, cpu_identifier),
        hashlib.sha256(machine_token.encode()).hexdigest(),
        hashlib.sha256(cpu_identifier.encode()).hexdigest(),
    )
