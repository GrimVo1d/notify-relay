# ADR-0004: `message.id` — ULID stored as a `CharField(26)`

**Status:** Accepted
**Date:** 2026-02-28

## Context

`Message` is the main hot table. At the expected peak load of 200 RPS (~17M rows/day at the upper bound) the primary key choice matters: it determines cluster locality on indexes, B-tree index size, and how easy log/trace correlation is.

## Decision

`id = CharField(primary_key=True, max_length=26, default=lambda: str(ULID()))`.

ULID is a 128-bit identifier: 48-bit millisecond timestamp + 80 bits of cryptographic randomness. Encoded in Crockford base32 it fits in 26 ASCII characters. It sorts lexicographically = chronologically.

## Consequences

**+** Lexicographically sortable = new rows append at the end of the index = sequential inserts = fewer B-tree splits and less WAL than UUIDv4.
**+** Embeds a timestamp — you can read approximate creation time from `id` alone, handy in logs and in the `X-Notify-Message-Id` header.
**+** 26-character ASCII is easy to drop in URLs, logs, or copy/paste. The hyphenated UUID format is visually worse.
**+** Adjacent ids are not guessable (unlike autoincrement) — you can't scrape someone else's messages by enumeration.
**−** 26 bytes instead of 8 (bigint) in the index and FKs. At 17M rows/day that's ~430MB/year just for the PK column. Acceptable, but not free.
**−** It's not a UUID, so it doesn't play nicely with tools that expect the UUID format out of the box (Django Admin, OpenAPI generators). Solved with `CharField` + a hand-written serializer field.

## Alternatives

1. **`bigint` autoincrement.** Most compact, best for B-tree locality. Downside — ids are predictable, and cross-service correlation needs prefixes. Using identifiers as external API tokens is a bad idea in general.
2. **UUIDv4.** Standard, but random data → scattered inserts across the index → higher IO. Still OK at our volume, but the 2024+ trend is to avoid v4 for hot-table PKs.
3. **UUIDv7.** Same time-sortable profile as ULID, RFC 9562, native `UUIDField`. **A strong candidate**, and if we were starting in 2025+ with library support it would be the pick. Today ULID wins because `python-ulid` has been stable for 4+ years, while UUIDv7 in Python is still fragmented (`uuid6`, `uuid-utils`, `uuid7` — three different packages, none "official").
4. **Snowflake (Twitter-style).** Requires an id generator with an epoch and worker-id. Extra coordination for the same properties.

## Migration path to UUIDv7

If the Python standard library gains `uuid.uuid7()`:
1. Swap the default function: `default=lambda: uuid7().hex` (32 hex chars without dashes = a `CharField(32)`).
2. Old ULIDs stay as-is (valid ASCII identifiers).
3. No data migration required.

## Related

- [`src/apps/messages_api/models.py::_new_ulid`](../../src/apps/messages_api/models.py)
