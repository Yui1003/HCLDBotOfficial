from discord.ext import tasks
from clan_checker import check_members, get_clan_members, check_clan_membership

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

import os

from database import (
    setup_database,
    add_user,
    mark_removed,
    get_verified_users,
    get_user_by_discord_id,
    get_active_user_by_game_id
)


load_dotenv()


TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = int(
    os.getenv("GUILD_ID")
)

LOG_CHANNEL_ID = int(
    os.getenv("LOG_CHANNEL_ID")
)

MEMBER_ROLE_NAME = "Member"

ENABLE_KICK = (
    os.getenv(
        "ENABLE_KICK",
        "false"
    ).lower() == "true"
)


intents = discord.Intents.default()

intents.members = True
intents.message_content = True


class ClanGuard(commands.Bot):

    async def setup_hook(self):

        await setup_database()

        guild = discord.Object(
            id=GUILD_ID
        )

        # Copy the (currently global) commands into guild scope
        # BEFORE wiping the global ones, so they aren't lost.
        self.tree.copy_global_to(
            guild=guild
        )

        # Now clear the old global registrations so they stop
        # showing up as duplicates (run once, then this list stays empty).
        self.tree.clear_commands(
            guild=None
        )

        await self.tree.sync()  # pushes the empty global list -> deletes old globals

        synced = await self.tree.sync(
            guild=guild
        )  # pushes the guild-scoped copies -> instant, no dupes

        print(
            f"Synced {len(synced)} guild commands"
        )


bot = ClanGuard(
    command_prefix="!",
    intents=intents
)


processed_players = set()



@tasks.loop(seconds=10)
async def clan_check():

    try:

        players = await check_members()


    except Exception as e:

        print(
            f"Checker error: {e}"
        )

        return


    if not players:

        return


    guild = bot.get_guild(
        GUILD_ID
    )


    if guild is None:

        print(
            "Guild not found"
        )

        return


    log_channel = bot.get_channel(
        LOG_CHANNEL_ID
    )


    for player in players:

        discord_id = player["discord_id"]


        if discord_id in processed_players:

            continue


        processed_players.add(
            discord_id
        )


        member = guild.get_member(
            discord_id
        )


        if member is None:

            continue


        print(
            f"Detected removal candidate: "
            f"{player['ign']} "
            f"({player['game_id']})"
        )


        if not ENABLE_KICK:


            print(
                "Kick disabled. Dry-run mode."
            )


            if log_channel:

                await log_channel.send(
                    f"🧪 **Dry Run Detection**\n\n"
                    f"Player: `{player['ign']}`\n"
                    f"Ninja Saga ID: `{player['game_id']}`\n"
                    f"Discord: {member.mention}\n\n"
                    f"Kick disabled."
                )


            continue



        if member.id == guild.owner_id:

            print(
                "Skipped server owner."
            )

            continue



        if not guild.me.guild_permissions.kick_members:

            print(
                "Bot missing Kick Members permission."
            )

            continue



        try:

            await member.kick(
                reason=
                "No longer in Hidden Cloud Village"
            )


            await mark_removed(
                discord_id
            )


            print(
                f"Kicked {member}"
            )


            if log_channel:

                await log_channel.send(
                    f"🚪 **Automatic Clan Removal**\n\n"
                    f"Player: `{player['ign']}`\n"
                    f"Ninja Saga ID: `{player['game_id']}`\n"
                    f"Discord: {member.mention}\n\n"
                    f"Reason: No longer in Hidden Cloud Village"
                )


        except Exception as e:

            print(
                f"Kick failed: {e}"
            )






class AccessServerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Access Server", emoji="🚪", style=discord.ButtonStyle.green, custom_id="access_server_button")
    async def access_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = discord.utils.get(interaction.guild.roles, name=MEMBER_ROLE_NAME)
        if role is None:
            await interaction.response.send_message("❌ Member role not found.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.response.send_message("✅ You already have access.", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role)
            await interaction.response.send_message("🎉 Welcome! You now have access to the server.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to assign roles.", ephemeral=True)


@bot.event
async def on_ready():

    bot.add_view(AccessServerView())

    print(
        f"Logged in as {bot.user}"
    )

    print(
        "Clan Guard online"
    )


    print(
        f"Kick mode: {ENABLE_KICK}"
    )


    if not clan_check.is_running():

        clan_check.start()






@bot.tree.command(
    name="verify",
    description="Link your Discord account with your Ninja Saga ID"
)
@app_commands.describe(
    game_id="Your Ninja Saga User ID",
    ign="Your Ninja Saga character name"
)
async def verify(
    interaction: discord.Interaction,
    game_id: int,
    ign: str
):

    # Talking to the clan API can take a moment, so acknowledge
    # the interaction immediately to avoid a 3s timeout.
    await interaction.response.defer()


    # 1) This Discord account already has an active link?
    existing = await get_user_by_discord_id(
        interaction.user.id
    )

    if existing and not existing[4]:  # existing[4] == removed

        await interaction.followup.send(
            f"❌ Your Discord account is already linked to "
            f"Ninja Saga ID `{existing[1]}` (IGN: `{existing[2]}`).\n\n"
            f"If this needs to change, please ask an admin to update it "
            f"with `/modifyverify`."
        )

        return


    # 2) Is this game_id already claimed by a *different* Discord account?
    conflict = await get_active_user_by_game_id(
        game_id
    )

    if conflict and conflict[0] != interaction.user.id:

        await interaction.followup.send(
            f"❌ Ninja Saga ID `{game_id}` is already linked to another "
            f"Discord account.\n\n"
            f"If this is a mistake, please contact an admin."
        )

        return


    # 3) Validate against the live Hidden Cloud Village member list
    result = await check_clan_membership(
        game_id,
        ign
    )

    if result["status"] == "error":

        await interaction.followup.send(
            "⚠️ Couldn't reach the clan API right now. Please try again "
            "in a moment."
        )

        return


    if result["status"] == "not_found":

        await interaction.followup.send(
            f"❌ Ninja Saga ID `{game_id}` was not found in Hidden Cloud "
            f"Village's member list.\n\n"
            f"Make sure you're in the clan and that you entered the "
            f"correct ID."
        )

        return


    if result["status"] == "name_mismatch":

        await interaction.followup.send(
            f"❌ That ID belongs to Hidden Cloud Village, but the IGN "
            f"you entered doesn't match.\n\n"
            f"The registered in-game name for that ID is: "
            f"`{result['actual_name']}`\n\n"
            f"Please try again with that exact name."
        )

        return


    # result["status"] == "ok"

    await add_user(
        interaction.user.id,
        game_id,
        ign
    )


    embed = discord.Embed(
        title="✅ Verification Successful",
        description=(
            f"Welcome, {interaction.user.mention}!\n\n"
            f"**IGN:** `{ign}`\n"
            f"**Ninja Saga ID:** `{game_id}`\n\n"
            "Click the **Access Server** button below to unlock the server."
        ),
        color=discord.Color.green()
    )

    await interaction.followup.send(embed=embed, view=AccessServerView())






@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(
    name="clancheck",
    description="Check Hidden Cloud Village API members"
)
async def clancheck(
    interaction: discord.Interaction
):

    members = await get_clan_members()


    await interaction.response.send_message(
        f"☁️ **Hidden Cloud Village API Check**\n\n"
        f"Members found: `{len(members)}`\n\n"
        f"`{members[:50]}`"
    )






@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(
    name="verified",
    description="Show all verified Hidden Cloud Village members"
)
async def verified(
    interaction: discord.Interaction
):

    users = await get_verified_users()


    if not users:

        await interaction.response.send_message(
            "No verified players found."
        )

        return



    embed = discord.Embed(
        title="☁️ Verified Clan Members",
        color=discord.Color.blue()
    )


    for discord_id, game_id, ign in users:


        member = interaction.guild.get_member(
            discord_id
        )


        if member:

            discord_name = member.display_name

        else:

            discord_name = "Unknown"



        embed.add_field(
            name=f"{game_id} - {ign}",
            value=f"Discord: {discord_name}",
            inline=False
        )


    await interaction.response.send_message(
        embed=embed
    )


@app_commands.checks.has_permissions(administrator=True)
@bot.tree.command(
    name="modifyverify",
    description="Admin: change a user's verified IGN and/or Ninja Saga ID"
)
@app_commands.describe(
    member="The Discord member whose verification to modify",
    new_game_id="New Ninja Saga ID (leave empty to keep current)",
    new_ign="New IGN (leave empty to keep current)",
    force="Bypass the duplicate-ID safety check (default: off)"
)
async def modifyverify(
    interaction: discord.Interaction,
    member: discord.Member,
    new_game_id: int = None,
    new_ign: str = None,
    force: bool = False
):

    if new_game_id is None and new_ign is None:

        await interaction.response.send_message(
            "❌ You need to provide at least one of `new_game_id` or "
            "`new_ign` to change.",
            ephemeral=True
        )

        return


    current = await get_user_by_discord_id(
        member.id
    )

    if current is None:

        await interaction.response.send_message(
            f"❌ {member.mention} isn't verified yet. To enroll them, "
            f"provide both `new_game_id` and `new_ign`.",
            ephemeral=True
        )

        if new_game_id is None or new_ign is None:

            return


        current = (member.id, None, None, 0, 0)


    old_game_id = current[1]
    old_ign = current[2]

    resolved_game_id = (
        new_game_id
        if new_game_id is not None
        else old_game_id
    )

    resolved_ign = (
        new_ign
        if new_ign is not None
        else old_ign
    )


    if new_game_id is not None and not force:

        conflict = await get_active_user_by_game_id(
            resolved_game_id
        )

        if conflict and conflict[0] != member.id:

            await interaction.response.send_message(
                f"❌ Ninja Saga ID `{resolved_game_id}` is already "
                f"linked to another Discord account (<@{conflict[0]}>, "
                f"IGN: `{conflict[2]}`).\n\n"
                f"If this is intentional, re-run the command with "
                f"`force: True`.",
                ephemeral=True
            )

            return


    await add_user(
        member.id,
        resolved_game_id,
        resolved_ign
    )


    await interaction.response.send_message(
        f"✅ Updated verification for {member.mention}\n\n"
        f"Ninja Saga ID: `{old_game_id}` → `{resolved_game_id}`\n"
        f"IGN: `{old_ign}` → `{resolved_ign}`"
    )




@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        await interaction.response.send_message(
            "❌ You do not have permission to use this command.",
            ephemeral=True
        )

        return

    raise error


bot.run(TOKEN)
