import aiohttp
import os
import re
import unicodedata

from dotenv import load_dotenv

from database import (
    get_users,
    update_missing,
    update_ign,
    normalize_ign
)


load_dotenv()


API_URL = "https://static.ninjasaga.cc/data/clan_rankings.json"

CLAN_NAME = os.getenv(
    "CLAN_NAME",
    "Hidden Cloud Village"
)


# ---------------------------------------------------------------------------
# Name matching
#
# The API no longer exposes user IDs or clan IDs, so a player is identified
# purely by the in-game name listed in the Hidden Cloud Village member_list.
# Matching is done in tiers, from strictest to most forgiving. A name only
# counts if it resolves to exactly ONE clan member.
# ---------------------------------------------------------------------------

def _loose_key(name: str) -> str:
    """
    Ignores case, spacing, accents/combining marks, invisible characters
    and punctuation/symbols (e.g. the tag decorations and stars used in
    Ninja Saga names). Returns "" if nothing meaningful is left.
    """

    name = unicodedata.normalize("NFKC", name or "")

    return "".join(
        ch for ch in name
        if unicodedata.category(ch)[0] in ("L", "N")
    ).casefold()


def _without_tag(name: str) -> str:
    """Drops a leading clan tag: 'XX Eliana' -> 'Eliana'."""

    parts = re.split(r"\s+", (name or "").strip(), maxsplit=1)

    if len(parts) == 2:

        return parts[1]

    return ""


def find_member(ign: str, member_names: list):
    """
    Finds `ign` in the clan roster.

    Returns a dict:
        {"status": "ok", "name": <exact name used by the game>}
        {"status": "ambiguous", "candidates": [names...]}
        {"status": "not_found"}
    """

    typed_exact = normalize_ign(ign)

    typed_loose = _loose_key(ign)

    if not typed_exact:

        return {"status": "not_found"}

    tiers = [
        lambda n: normalize_ign(n) == typed_exact,
    ]

    if typed_loose:

        tiers.append(
            lambda n: _loose_key(n) == typed_loose
        )

        tiers.append(
            lambda n: _loose_key(_without_tag(n)) == typed_loose
        )

    for matches_tier in tiers:

        hits = [
            name for name in member_names
            if matches_tier(name)
        ]

        if len(hits) == 1:

            return {
                "status": "ok",
                "name": hits[0]
            }

        if len(hits) > 1:

            return {
                "status": "ambiguous",
                "candidates": hits[:5]
            }

    return {
        "status": "not_found"
    }


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------

async def _fetch_roster():
    """
    Returns (status, names):

        ("ok", [names])       roster fetched
        ("error", None)       API unreachable / bad response
        ("no_clan", None)     API fine, but the clan isn't in it
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

                    return "error", None

                # content_type=None: don't fail if the static host serves
                # the JSON with a generic content type.
                data = await response.json(
                    content_type=None
                )

    except Exception as e:

        print(
            f"API connection error: {e}"
        )

        return "error", None


    try:

        target = normalize_ign(CLAN_NAME)

        for clan in data.get("clans", []):

            if normalize_ign(clan.get("name", "")) == target:

                names = [
                    member["name"]
                    for member in clan.get("member_list", [])
                    if isinstance(member, dict)
                    and isinstance(member.get("name"), str)
                    and member["name"].strip()
                ]

                return "ok", names

    except Exception as e:

        print(
            f"API payload error: {e}"
        )

        return "error", None


    print(
        f"Clan not found: {CLAN_NAME}"
    )

    return "no_clan", None


async def get_clan_members():
    """
    List of member names (str) currently in CLAN_NAME.

    Returns [] if the API could not be reached or the clan wasn't found,
    so callers must treat an empty list as "could not verify", never as
    "the clan is empty".
    """

    status, names = await _fetch_roster()

    if status != "ok":

        return []

    return names


async def check_clan_membership(ign: str):
    """
    Validates an IGN against the live Hidden Cloud Village member list.

    Returns a dict with a "status" key, one of:

        "ok"         - ign is a member. Includes "name": the exact in-game
                       spelling, which is what should be stored.
        "not_found"  - no member of the clan has that name.
        "ambiguous"  - more than one member matches; the user must type the
                       exact name. Includes "candidates".
        "error"      - couldn't reach / parse the API right now, or the clan
                       wasn't in the data. Nobody is verified in this case.
    """

    status, names = await _fetch_roster()

    if status != "ok" or not names:

        return {
            "status": "error"
        }

    return find_member(
        ign,
        names
    )


async def check_members():

    clan_members = await get_clan_members()


    if not clan_members:

        return []



    users = await get_users()

    results = []



    for (
        discord_id,
        ign,
        missing,
        removed
    ) in users:



        # Already removed, ignore

        if removed:

            continue



        match = find_member(
            ign,
            clan_members
        )


        # "ambiguous" still means someone with that name is in the clan,
        # so it is never treated as a departure.
        if match["status"] == "not_found":

            missing += 1

        else:

            missing = 0


            # Migrated records may hold an older spelling of the name.
            # Refresh it to the game's exact spelling when that's safe.
            if (
                match["status"] == "ok"
                and match["name"] != ign
            ):

                await update_ign(
                    discord_id,
                    match["name"]
                )



        await update_missing(
            discord_id,
            missing
        )



        # Require 3 failed checks in a row
        # 3 x 10 seconds = about 30 seconds

        if missing >= 3:

            results.append(
                {
                    "discord_id": discord_id,
                    "ign": ign
                }
            )



    return results
