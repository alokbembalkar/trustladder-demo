"""
Cryptographic building blocks: slow salted codes and issuer signatures.

Two ideas from the proposal live here.

1. The "scrambled code" (a hash).
   The hospital never publishes a bill. For each bill it publishes two codes:

       key   = code(ticket)                        -> "does this ticket exist?"
       value = code(ticket | bill no | date | patient | amount)
                                                   -> "are the details as issued?"

   The code is computed with scrypt, a deliberately slow function, mixed with a
   per-issuer salt. The same inputs always give the same code, any change gives a
   completely different code, and the code cannot be run backwards into the
   details.

   Privacy comes mainly from the ticket: a 16-character random reference printed
   only on the bill. Without it, somebody could guess dates and amounts and test
   the guesses. With it there is nothing to guess, so an entry can only be checked
   by someone who already holds the bill.

   Note on the salt: the verifier must recompute the code from its copy of the
   bill, so in this design the per-issuer salt is published in the issuer
   directory. Its job is to keep every issuer's codes separate (the same ticket at
   two hospitals gives unrelated codes, and nobody can precompute a table that
   works for every issuer). It is not a secret.

2. The issuer's signature (Ed25519).
   Each published file is signed with the hospital's private key, which stays
   inside the hospital. Anyone can check the signature with the public key held
   in the directory, so a registry operator or a mirror cannot forge or quietly
   alter the file.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization

# scrypt cost parameters. n = 2**13 takes roughly 10-20 ms on a laptop: invisible
# for one lookup, expensive for anyone trying millions of guesses. A production
# deployment would tune this upwards.
SCRYPT_N = 2 ** 13
SCRYPT_R = 8
SCRYPT_P = 1
CODE_BYTES = 32

# Alphabet for the printed ticket: upper-case letters and digits, minus the
# characters people misread on paper (0/O, 1/I/L). 31 symbols ^ 16 characters
# is about 2^79 possibilities, far beyond guessing.
TICKET_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
TICKET_LENGTH = 16


def new_ticket(prefix: str) -> str:
    """Return a fresh random ticket such as 'SH-7K2P-9QX4-8M31-RT5W'.

    The prefix identifies the issuer to a human reader; the 16 random characters
    are what make the ticket unguessable. Grouped in fours for legibility.
    """
    body = "".join(secrets.choice(TICKET_ALPHABET) for _ in range(TICKET_LENGTH))
    groups = [body[i:i + 4] for i in range(0, TICKET_LENGTH, 4)]
    return prefix + "-" + "-".join(groups)


def new_salt() -> str:
    """Return a fresh per-issuer salt as hex text (16 random bytes)."""
    return secrets.token_hex(16)


def code(text: str, salt_hex: str) -> str:
    """Return the slow salted code of `text` as a hex string.

    `text` must already be in canonical form (see reader.canonical_record), so
    that two spellings of the same value can never produce different codes.
    """
    digest = hashlib.scrypt(
        text.encode("utf-8"),
        salt=bytes.fromhex(salt_hex),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=CODE_BYTES,
    )
    return digest.hex()


# --------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------

def new_signing_key() -> tuple[str, str]:
    """Create an issuer key pair. Returns (private_key_b64, public_key_b64).

    The private key belongs to the hospital's publisher and never leaves it. The
    public key is placed in the issuer directory for everyone to use.
    """
    private = Ed25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(private_raw).decode(), base64.b64encode(public_raw).decode()


def canonical_json(payload: dict) -> bytes:
    """Serialise a dict in one fixed way (sorted keys, no spaces).

    Signing and verifying must see byte-identical input, so this is the only
    serialisation used for anything that gets signed.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(payload: dict, private_key_b64: str) -> str:
    """Sign the canonical form of `payload`; return the signature as base64."""
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    return base64.b64encode(private.sign(canonical_json(payload))).decode()


def verify_signature(payload: dict, signature_b64: str, public_key_b64: str) -> bool:
    """Return True only if `signature_b64` is the issuer's valid seal on `payload`."""
    try:
        public = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        public.verify(base64.b64decode(signature_b64), canonical_json(payload))
        return True
    except (InvalidSignature, ValueError):
        return False
