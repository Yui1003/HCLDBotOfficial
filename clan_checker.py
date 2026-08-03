import aiohttp
import os

from dotenv import load_dotenv

from database import (
    get_users,
    update_missing
)


load_dotenv()


API_URL = "https://static.ninjasaga.cc/data/clan_rankings.json"

CLAN_NAME = os.getenv(
    "CLAN_NAME",
    "Hidden Cloud Village"
)


def _normalize_name(name: str) -> str:
    """Trim + casefold so tiny formatting differences don't cause false mismatches."""

    return name.strip().casefold()


async def get_clan_data():
    """
    Fetches the live rankings JSON and returns the member_list
    (list of {"id", "level", "name", "reputation"}) for CLAN_NAME.

    Returns None if the API could not be reached, returned a bad
    status, or the clan could not be found in the payload — callers
    should treat None as "could not verify right now", not as
    "clan is empty".
    """

    try:

        timeout = aiohttp.ClientTimeout(
            total=10
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(API_URL) as response:

                if response.status != 200:

                    print(
                        f"API returned HTTP {response.status}"
                    )

                    return None


                data = await response.json()


    except Exception as e:

        print(
            f"API connection error: {e}"
        )

        return None



    for clan in data.get("clans", []):

        if clan.get("name") == CLAN_NAME:

            return clan.get(
                "member_list",
                []
            )


    print(
        f"Clan not found: {CLAN_NAME}"
    )

    return None



async def get_clan_members():
    """Backwards-compatible helper: just the list of member IDs (ints)."""

    member_list = await get_clan_data()

    if member_list is None:

        return []


    return [
        member["id"]
        for member in member_list
    ]



async def check_clan_membership(game_id: int, ign: str):
    """
    Validates a (game_id, ign) pair against the live Hidden Cloud
    Village member list.

    Returns a dict with a "status" key, one of:

        "ok"            - game_id is in the clan and ign matches
        "not_found"     - game_id is not in the clan's member list
        "name_mismatch" - game_id is in the clan, but the ign given
                           doesn't match. Includes "actual_name".
        "error"         - couldn't reach / parse the API right now.
    """

    member_list = await get_clan_data()

    if member_list is None:

        return {
            "status": "error"
        }


    for member in member_list:

        if member.get("id") == game_id:

            actual_name = member.get(
                "name",
                ""
            )


            if _normalize_name(actual_name) == _normalize_name(ign):

                return {
                    "status": "ok"
                }


            return {
                "status": "name_mismatch",
                "actual_name": actual_name
            }


    return {
        "status": "not_found"
    }




async def check_members():

    clan_members = await get_clan_members()


    if not clan_members:

        return []



    users = await get_users()

    results = []



    for (
        discord_id,
        game_id,
        ign,
        missing,
        removed
    ) in users:



        # Already removed, ignore

        if removed:

            continue



        if game_id not in clan_members:

            missing += 1

        else:

            missing = 0



        await update_missing(
            discord_id,
            missing
        )



        # Require 6 failed checks
        # 6 x 10 seconds = about 1 minute

        if missing >= 3:

            results.append(
                {
                    "discord_id": discord_id,
                    "game_id": game_id,
                    "ign": ign
                }
            )



    return results
