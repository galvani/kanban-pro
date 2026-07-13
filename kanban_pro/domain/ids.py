"""Card id schemes — what a card's id LOOKS like, chosen per board (`board.id_scheme`).

A `uuid4().hex` card id is 32 characters, and every tool call and every human reference to
a card carries it. So the shape is a board setting, administered like the rest of the board
(`create_board` / `init_board` / `update_board`), NOT a server flag or an env var — same
reasoning that moved the workflow onto the board: one store, one admin path.

    None / "uuid"   9f3c1a…       32 hex — the default, unchanged
    short[:N]       k7f3q9xw      N random chars, N=4..32, default 10
    prefix:KAN[:N]  KAN-k7f3q9    the same behind a prefix, N default 6
    seq:KAN         KAN-1, KAN-2  a per-board counter — shortest, and the only ordered one

`short`/`prefix` draw from a Crockford-style base32 alphabet (no i/l/o/u) so an id can be
read aloud and re-typed without ambiguity; at N=8 that is 40 bits, plenty for one board.

The id is minted BY THE STORE, in `create_card`: only the store can read the scheme off the
board the card lands on, and only the store can bump a `seq:` counter. A Card therefore
arrives with `id=""` ("mint me one") unless the caller pins an id explicitly — which
migration does, to preserve the source's ids. The scheme governs CARD ids only; board,
column and comment ids stay uuids (plumbing nobody quotes, and a `seq:` counter shared
across entity kinds would read as if a board and a card were the same ticket).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import uuid4

#: Crockford base32 minus the ambiguous glyphs (i, l, o, u) — safe to read aloud/re-type.
_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_MIN_LEN, _MAX_LEN = 4, 32


class InvalidScheme(ValueError):
    """A board's `id_scheme` could not be parsed."""


@dataclass(frozen=True)
class IdScheme:
    """A parsed `board.id_scheme`. `store_assigned` schemes need a counter, not a generator."""

    kind: str  # uuid | short | prefix | seq
    prefix: str = ""
    length: int = 0

    @property
    def store_assigned(self) -> bool:
        """True for `seq:` — the store must count, so `generate()` can't answer alone."""
        return self.kind == "seq"

    def generate(self) -> str:
        """A fresh id. Never call this for a `seq:` scheme (see `store_assigned`)."""
        if self.kind == "uuid":
            return uuid4().hex
        if self.store_assigned:
            raise InvalidScheme("a seq: id is counted by the store, not generated")
        body = "".join(secrets.choice(_ALPHABET) for _ in range(self.length))
        return f"{self.prefix}-{body}" if self.prefix else body


UUID_SCHEME = IdScheme("uuid")


def _length(raw: str, default: int) -> int:
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        raise InvalidScheme(f"id length {raw!r} is not a number") from None
    if not _MIN_LEN <= n <= _MAX_LEN:
        raise InvalidScheme(f"id length {n} out of range ({_MIN_LEN}..{_MAX_LEN})")
    return n


def parse_scheme(spec: str | None) -> IdScheme:
    """Parse a `board.id_scheme`; None/empty -> uuid. Raises InvalidScheme on anything else.

    Called by Board's validator, so a bad scheme is refused at create_board/update_board —
    never at the first card, on a board that already exists.
    """
    if not (spec := (spec or "").strip()):
        return UUID_SCHEME
    kind, _, rest = spec.partition(":")
    kind = kind.lower()
    if kind == "uuid" and not rest:
        return UUID_SCHEME
    if kind == "short":
        return IdScheme("short", length=_length(rest, 10))
    if kind in ("prefix", "seq"):
        prefix, _, tail = rest.partition(":")
        if not prefix:
            raise InvalidScheme(f"{kind!r} needs a prefix, e.g. {kind}:KAN")
        if kind == "seq":
            if tail:
                raise InvalidScheme("seq takes no length (its ids count 1, 2, 3…)")
            return IdScheme("seq", prefix=prefix)
        return IdScheme("prefix", prefix=prefix, length=_length(tail, 6))
    raise InvalidScheme(f"unknown id scheme {spec!r} (known: uuid, short[:N], prefix:P[:N], seq:P)")


def new_id() -> str:
    """Id for everything that is NOT a card (board, column, comment, …) — always a uuid."""
    return uuid4().hex
