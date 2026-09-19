import os
import re
import shutil
import sqlite3
import unicodedata
from datetime import datetime

import aiosqlite


DB_NAME = "clan_guard.db"


# ---------------------------------------------------------------------------
# Name handling
# ---------------------------------------------------------------------------

def normalize_ign(name: str) -> str:
    """
    Canonical comparison key for an in-game name.

    The Ninja Saga API no longer has user/clan IDs, so the IGN *is* the
    identity. This makes tiny formatting differences (case, extra spaces,
    invisible zero-width characters, full-width letters) irrelevant while
    keeping different names different.
    """

    name = unicodedata.normalize("NFKC", name or "")

    # Drop invisible "format" characters (zero-width joiners, etc.)
    name = "".join(
        ch for ch in name
        if unicodedata.category(ch) != "Cf"
    )

    name = re.sub(r"\s+", " ", name).strip()

    return name.casefold()


class DuplicateIgnError(Exception):
    """Raised when an IGN is already linked to a different Discord account."""

    def __init__(self, holder_discord_id):

        super().__init__(
            f"IGN already linked to Discord ID {holder_discord_id}"
        )

        self.holder_discord_id = holder_discord_id


# Filled in by setup_database() if a legacy database was migrated.
# bot.py reads this once on startup and posts it to the log channel.
MIGRATION_REPORT = None


# ---------------------------------------------------------------------------
# Schema + migration
# ---------------------------------------------------------------------------

_CREATE_USERS_SQL = """
CREATE TABLE {name} (
    discord_id INTEGER PRIMARY KEY,
    ign TEXT NOT NULL,
    ign_key TEXT NOT NULL,
    legacy_game_id INTEGER,
    missing_checks INTEGER DEFAULT 0,
    removed INTEGER DEFAULT 0
)
"""

# One ACTIVE record per game name. Removed records are kept for history
# and are allowed to share a name.
_CREATE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_active_ign
ON users (ign_key)
WHERE removed = 0
"""


def _migrate_sync():
    """
    Creates the table on a fresh install, or upgrades a legacy
    (game_id-based) database to the IGN-based schema.

    The upgrade is:
      * backed up first (clan_guard.db.pre-ign-migration-<timestamp>)
      * done in a single transaction (all-or-nothing)
      * idempotent (does nothing if already migrated)
      * de-duplicated: if several old rows resolve to the same game name,
        only ONE stays active. The others are kept but marked removed, and
        listed in the migration report so an admin can review them.
    """

    report = None

    conn = sqlite3.connect(DB_NAME)

    try:

        columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(users)")
        ]

        if not columns:

            conn.execute(_CREATE_USERS_SQL.format(name="users"))

            conn.execute(_CREATE_INDEX_SQL)

            conn.commit()

            return None


        if "ign_key" in columns:

            # Already on the new schema.
            conn.execute(_CREATE_INDEX_SQL)

            conn.commit()

            return None


        # ---- Legacy database: migrate -----------------------------------

        conn.close()

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

        backup_path = f"{DB_NAME}.pre-ign-migration-{stamp}"

        shutil.copy2(DB_NAME, backup_path)

        conn = sqlite3.connect(DB_NAME)

        conn.isolation_level = None  # manual transaction control

        old_rows = conn.execute(
            """
            SELECT discord_id, game_id, ign, missing_checks, removed
            FROM users
            """
        ).fetchall()

        # Group by normalized name.
        groups = {}

        skipped_blank = []

        for (discord_id, game_id, ign, missing, removed) in old_rows:

            key = normalize_ign(ign)

            if not key:

                skipped_blank.append(discord_id)

                continue

            groups.setdefault(key, []).append(
                (discord_id, game_id, ign, missing or 0, removed or 0)
            )

        new_rows = []

        duplicates = []

        for key, rows in groups.items():

            # Winner: active before removed, then the record that was most
            # recently confirmed in the clan (lowest missing_checks), then
            # the oldest Discord account (lowest ID) as a stable tie-break.
            rows.sort(
                key=lambda r: (
                    1 if r[4] else 0,
                    r[3],
                    r[0]
                )
            )

            winner = rows[0]

            new_rows.append(
                (winner[0], winner[2], key, winner[1], winner[3], winner[4])
            )

            for loser in rows[1:]:

                was_active = not loser[4]

                # Losers are always kept, but never active.
                new_rows.append(
                    (loser[0], loser[2], key, loser[1], loser[3], 1)
                )

                if was_active and not winner[4]:

                    duplicates.append(
                        {
                            "ign": loser[2],
                            "discord_id": loser[0],
                            "kept_discord_id": winner[0]
                        }
                    )

        for discord_id in skipped_blank:

            original = next(
                r for r in old_rows if r[0] == discord_id
            )

            new_rows.append(
                (discord_id, original[2], "", original[1], original[3] or 0, 1)
            )

        conn.execute("BEGIN")

        try:

            conn.execute(_CREATE_USERS_SQL.format(name="users_new"))

            conn.executemany(
                """
                INSERT INTO users_new
                (discord_id, ign, ign_key, legacy_game_id,
                 missing_checks, removed)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                new_rows
            )

            conn.execute("DROP TABLE users")

            conn.execute("ALTER TABLE users_new RENAME TO users")

            conn.execute(_CREATE_INDEX_SQL)

            conn.execute("COMMIT")

        except Exception:

            conn.execute("ROLLBACK")

            raise

        report = {
            "backup": backup_path,
            "total_old": len(old_rows),
            "total_new": len(new_rows),
            "active": sum(1 for r in new_rows if not r[5]),
            "duplicates": duplicates,
            "blank": skipped_blank
        }

        return report

    finally:

        conn.close()


async def setup_database():

    global MIGRATION_REPORT

    MIGRATION_REPORT = _migrate_sync()

    if MIGRATION_REPORT:

        print(
            f"Database migrated to IGN-based records. "
            f"Backup: {MIGRATION_REPORT['backup']} | "
            f"{MIGRATION_REPORT['total_old']} old rows -> "
            f"{MIGRATION_REPORT['total_new']} rows "
            f"({MIGRATION_REPORT['active']} active), "
            f"{len(MIGRATION_REPORT['duplicates'])} duplicate(s) deactivated."
        )


def pop_migration_report():
    """Returns the startup migration report once, then clears it."""

    global MIGRATION_REPORT

    report = MIGRATION_REPORT

    MIGRATION_REPORT = None

    return report


# ---------------------------------------------------------------------------
# Queries
#
# Every user row is returned as:
#   (discord_id, ign, missing_checks, removed)
# ---------------------------------------------------------------------------

_USER_COLUMNS = "discord_id, ign, missing_checks, removed"


async def add_user(
    discord_id,
    ign,
    force=False
):
    """
    Links a Discord account to an IGN (creating or replacing the record).

    Guarantees one ACTIVE Discord account per game name. The check is
    enforced by a unique index, so two simultaneous /verify calls cannot
    both succeed.

    If the name is already active on a different Discord account:
      * force=False -> raises DuplicateIgnError
      * force=True  -> the other account's record is marked removed and the
                       name is moved to this account
    """

    key = normalize_ign(ign)

    if not key:

        raise ValueError("IGN cannot be empty")

    async with aiosqlite.connect(DB_NAME) as db:

        try:

            if force:

                await db.execute(
                    """
                    UPDATE users
                    SET removed = 1
                    WHERE ign_key = ?
                    AND removed = 0
                    AND discord_id != ?
                    """,
                    (key, discord_id)
                )

            await db.execute(
                """
                INSERT INTO users
                (discord_id, ign, ign_key, missing_checks, removed)

                VALUES (?, ?, ?, 0, 0)

                ON CONFLICT(discord_id)

                DO UPDATE SET
                    ign = excluded.ign,
                    ign_key = excluded.ign_key,
                    missing_checks = 0,
                    removed = 0
                """,
                (discord_id, ign, key)
            )

            await db.commit()

        except sqlite3.IntegrityError:

            await db.rollback()

            cursor = await db.execute(
                """
                SELECT discord_id
                FROM users
                WHERE ign_key = ?
                AND removed = 0
                AND discord_id != ?
                """,
                (key, discord_id)
            )

            holder = await cursor.fetchone()

            raise DuplicateIgnError(
                holder[0] if holder else None
            )


async def get_users():

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            f"SELECT {_USER_COLUMNS} FROM users"
        )

        return await cursor.fetchall()


async def get_user_by_discord_id(discord_id):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            f"""
            SELECT {_USER_COLUMNS}
            FROM users
            WHERE discord_id = ?
            """,
            (discord_id,)
        )

        return await cursor.fetchone()


async def get_active_user_by_ign(ign):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            f"""
            SELECT {_USER_COLUMNS}
            FROM users
            WHERE ign_key = ?
            AND removed = 0
            """,
            (normalize_ign(ign),)
        )

        return await cursor.fetchone()


async def get_user_by_ign(ign):
    """Any record (active preferred) for this IGN."""

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            f"""
            SELECT {_USER_COLUMNS}
            FROM users
            WHERE ign_key = ?
            ORDER BY removed ASC
            """,
            (normalize_ign(ign),)
        )

        return await cursor.fetchone()


async def get_verified_users():
    """Active users as (discord_id, ign)."""

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            """
            SELECT discord_id, ign
            FROM users
            WHERE removed = 0
            ORDER BY ign_key
            """
        )

        return await cursor.fetchall()


async def update_missing(
    discord_id,
    missing
):

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(
            """
            UPDATE users
            SET missing_checks = ?
            WHERE discord_id = ?
            """,
            (missing, discord_id)
        )

        await db.commit()


async def update_ign(
    discord_id,
    ign
):
    """
    Refreshes a stored IGN to the exact spelling used by the clan API
    (best effort). Returns False if it would collide with another active
    record, in which case nothing is changed.
    """

    async with aiosqlite.connect(DB_NAME) as db:

        try:

            await db.execute(
                """
                UPDATE users
                SET ign = ?, ign_key = ?
                WHERE discord_id = ?
                """,
                (ign, normalize_ign(ign), discord_id)
            )

            await db.commit()

            return True

        except sqlite3.IntegrityError:

            await db.rollback()

            return False


async def mark_removed(
    discord_id
):

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(
            """
            UPDATE users
            SET removed = 1
            WHERE discord_id = ?
            """,
            (discord_id,)
        )

        await db.commit()


async def delete_user_by_ign(
    ign
):
    """Deletes every record for this IGN. Returns the deleted rows."""

    key = normalize_ign(ign)

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            f"""
            SELECT {_USER_COLUMNS}
            FROM users
            WHERE ign_key = ?
            """,
            (key,)
        )

        rows = await cursor.fetchall()

        if not rows:

            return []

        await db.execute(
            "DELETE FROM users WHERE ign_key = ?",
            (key,)
        )

        await db.commit()

        return rows


async def delete_user_by_discord_id(
    discord_id
):
    """Deletes the record for this Discord account. Returns it (or None)."""

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            f"""
            SELECT {_USER_COLUMNS}
            FROM users
            WHERE discord_id = ?
            """,
            (discord_id,)
        )

        user = await cursor.fetchone()

        if user is None:

            return None

        await db.execute(
            "DELETE FROM users WHERE discord_id = ?",
            (discord_id,)
        )

        await db.commit()

        return user
