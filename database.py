import aiosqlite


DB_NAME = "clan_guard.db"


async def setup_database():

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (

            discord_id INTEGER PRIMARY KEY,

            game_id INTEGER NOT NULL,

            ign TEXT NOT NULL,

            missing_checks INTEGER DEFAULT 0,

            removed INTEGER DEFAULT 0

        )
        """)

        await db.commit()



async def add_user(
    discord_id,
    game_id,
    ign
):

    async with aiosqlite.connect(DB_NAME) as db:

        await db.execute(
            """
            INSERT INTO users
            (
                discord_id,
                game_id,
                ign,
                missing_checks,
                removed
            )

            VALUES (?, ?, ?, 0, 0)

            ON CONFLICT(discord_id)

            DO UPDATE SET

                game_id = excluded.game_id,

                ign = excluded.ign,

                missing_checks = 0,

                removed = 0
            """,

            (
                discord_id,
                game_id,
                ign
            )
        )

        await db.commit()



async def get_users():

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            """
            SELECT
                discord_id,
                game_id,
                ign,
                missing_checks,
                removed

            FROM users
            """
        )

        return await cursor.fetchall()



async def get_user_by_discord_id(
    discord_id
):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            """
            SELECT
                discord_id,
                game_id,
                ign,
                missing_checks,
                removed

            FROM users

            WHERE discord_id = ?
            """,

            (
                discord_id,
            )
        )

        return await cursor.fetchone()



async def get_active_user_by_game_id(
    game_id
):

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            """
            SELECT
                discord_id,
                game_id,
                ign,
                missing_checks,
                removed

            FROM users

            WHERE game_id = ?
            AND removed = 0
            """,

            (
                game_id,
            )
        )

        return await cursor.fetchone()



# NEW COMMAND SUPPORT
async def get_verified_users():

    async with aiosqlite.connect(DB_NAME) as db:

        cursor = await db.execute(
            """
            SELECT
                discord_id,
                game_id,
                ign

            FROM users

            WHERE removed = 0

            ORDER BY game_id
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

            (
                missing,
                discord_id
            )
        )

        await db.commit()



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

            (
                discord_id,
            )
        )

        await db.commit()