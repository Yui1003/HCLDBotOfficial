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
    get_active_user_by_game_id,
    get_user_by_game_id,
    delete_user_by_game_id
)


load_dotenv()


TOKEN = os.getenv("DISCORD_TOKEN")

GUILD_ID = int(
    os.getenv("GUILD_ID")
)

LOG_CHANNEL_ID = int(
    os.getenv("LOG_CHANNEL_ID")
)

VERIFIED_CHANNEL_ID = int(
    os.getenv("VERIFIED_CHANNEL_ID")
)

WELCOME_CHANNEL_ID = int(
    os.getenv("WELCOME_CHANNEL_ID")
)

MEMBER_ROLE_NAME = "HCV"

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
        # showing up as duplicates.
        self.tree.clear_commands(
            guild=None
        )

        await self.tree.sync()

        synced = await self.tree.sync(
            guild=guild
        )

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


        member = guild.get_member(
            discord_id
        )


        print(
            f"Detected removal candidate: "
            f"{player['ign']} "
            f"({player['game_id']})"
        )


        # Dry-run mode must not change the database or kick anyone.
        if not ENABLE_KICK:

            if discord_id in processed_players:

                continue


            processed_players.add(
                discord_id
            )


            print(
                "Kick disabled. Dry-run mode."
            )


            if log_channel:

                if member is not None:

                    discord_target = member.mention

                else:

                    discord_target = (
                        f"<@{discord_id}> "
                        f"(already left the server)"
                    )


                await log_channel.send(
                    f"🧪 **Dry Run Detection**\n\n"
                    f"Player: `{player['ign']}`\n"
                    f"Ninja Saga ID: `{player['game_id']}`\n"
                    f"Discord: {discord_target}\n\n"
                    f"Kick disabled."
                )


            continue


        # The clan API is the source of truth.
        #
        # Mark the verification as removed even when
        # the Discord member has already left the server.
        await mark_removed(
            discord_id
        )


        print(
            f"Marked verification removed: "
            f"{player['ign']} ({player['game_id']})"
        )


        # If the user already left Discord, there is nothing to kick.
        if member is None:

            print(
                f"Discord member {discord_id} "
                f"is no longer in the server."
            )


            if log_channel:

                await log_channel.send(
                    f"🚪 **Automatic Clan Removal**\n\n"
                    f"Player: `{player['ign']}`\n"
                    f"Ninja Saga ID: `{player['game_id']}`\n"
                    f"Discord ID: `{discord_id}`\n\n"
                    f"The user had already left the Discord server. "
                    f"Their verification record was marked as removed."
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

        super().__init__(
            timeout=None
        )


    @discord.ui.button(
        label="Access Server",
        emoji="🚪",
        style=discord.ButtonStyle.green,
        custom_id="access_server_button"
    )
    async def access_server(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        role = discord.utils.get(
            interaction.guild.roles,
            name=MEMBER_ROLE_NAME
        )


        if role is None:

            await interaction.response.send_message(
                "❌ HCV role not found.",
                ephemeral=True
            )

            return


        if role in interaction.user.roles:

            await interaction.response.send_message(
                "✅ You already have access.",
                ephemeral=True
            )

            return


        try:

            await interaction.user.add_roles(
                role
            )


            # Send welcome message to #verified-members
            verified_channel = interaction.guild.get_channel(
                VERIFIED_CHANNEL_ID
            )


            if verified_channel:

                user = await get_user_by_discord_id(
                    interaction.user.id
                )


                ign = (
                    user[2]
                    if user
                    else "Unknown"
                )


                embed = discord.Embed(
                    title="🎉 New Clan Member!",
                    description=(
                        f"{interaction.user.mention} has joined "
                        f"**Hidden Cloud Village**!\n\n"
                        f"🥷 **IGN:** `{ign}`\n\n"
                        f"Everyone give them a warm welcome! 🎊"
                    ),
                    color=discord.Color.gold()
                )


                await verified_channel.send(
                    embed=embed
                )


            await interaction.response.send_message(
                "🎉 Welcome! You now have access to the server.",
                ephemeral=True
            )


        except discord.Forbidden:

            await interaction.response.send_message(
                "❌ I don't have permission to assign roles.",
                ephemeral=True
            )




@bot.event
async def on_ready():

    bot.add_view(
        AccessServerView()
    )


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




@app_commands.checks.has_permissions(
    administrator=True
)
@bot.tree.command(
    name="setupwelcome",
    description="Create and pin the welcome verification message."
)
async def setupwelcome(
    interaction: discord.Interaction
):

    # Prevent duplicate welcome messages
    pinned_messages = await interaction.channel.pins()


    for message in pinned_messages:

        if (
            message.author == bot.user
            and message.embeds
            and message.embeds[0].title
            == "👋 Welcome to Hidden Cloud Village!"
        ):

            await interaction.response.send_message(
                "✅ A welcome message is already pinned in this channel.",
                ephemeral=True
            )

            return


    embed = discord.Embed(
        title="👋 Welcome to Hidden Cloud Village!",
        description=(
            "Welcome to Hidden Cloud Village!\n\n"

            "Before you can access the server, "
            "please complete verification.\n\n"

            "**Step 1️⃣**\n"
            "Use the `/verify` command.\n\n"

            "**Step 2️⃣**\n"
            "Enter your **Ninja Saga User ID** and **IGN** "
            "exactly as they appear in-game.\n\n"

            "**Step 3️⃣**\n"
            "If verification succeeds, click the "
            "**🚪 Access Server** button to unlock "
            "the rest of the server.\n\n"

            "Need help? Contact a moderator."
        ),
        color=discord.Color.blurple()
    )


    embed.set_footer(
        text="Hidden Cloud Village Verification System"
    )


    message = await interaction.channel.send(
        embed=embed
    )


    await message.pin()


    await interaction.response.send_message(
        "✅ Welcome message created and pinned successfully.",
        ephemeral=True
    )




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

    # Talking to the clan API can take a moment,
    # so acknowledge the interaction immediately.
    await interaction.response.defer(
        ephemeral=True
    )


    # Ensure /verify is only used in the welcome channel
    if interaction.channel_id != WELCOME_CHANNEL_ID:

        await interaction.followup.send(
            f"❌ Please use `/verify` in "
            f"<#{WELCOME_CHANNEL_ID}>.",
            ephemeral=True
        )

        return


    # 1) This Discord account already has an active link?
    existing = await get_user_by_discord_id(
        interaction.user.id
    )


    if existing and not existing[4]:

        await interaction.followup.send(
            f"❌ Your Discord account is already linked to "
            f"Ninja Saga ID `{existing[1]}` "
            f"(IGN: `{existing[2]}`).\n\n"
            f"If this needs to change, please ask an admin "
            f"to update it with `/modifyverify`."
        )

        return


    # 2) Is this game_id already claimed by a different Discord account?
    conflict = await get_active_user_by_game_id(
        game_id
    )


    if conflict and conflict[0] != interaction.user.id:

        await interaction.followup.send(
            f"❌ Ninja Saga ID `{game_id}` is already linked "
            f"to another Discord account.\n\n"
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
            "⚠️ Couldn't reach the clan API right now. "
            "Please try again in a moment."
        )

        return


    if result["status"] == "not_found":

        await interaction.followup.send(
            f"❌ Ninja Saga ID `{game_id}` was not found "
            f"in Hidden Cloud Village's member list.\n\n"
            f"Make sure you're in the clan and that you "
            f"entered the correct ID."
        )

        return


    if result["status"] == "name_mismatch":

        await interaction.followup.send(
            f"❌ That ID belongs to Hidden Cloud Village, "
            f"but the IGN you entered doesn't match.\n\n"
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
            f"Click the **Access Server** button below "
            f"to unlock the server."
        ),
        color=discord.Color.green()
    )


    await interaction.followup.send(
        embed=embed,
        view=AccessServerView(),
        ephemeral=True
    )






@app_commands.checks.has_permissions(
    administrator=True
)
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






@app_commands.checks.has_permissions(
    administrator=True
)
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




@app_commands.checks.has_permissions(
    administrator=True
)
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
            "❌ You need to provide at least one of "
            "`new_game_id` or `new_ign` to change.",
            ephemeral=True
        )

        return


    current = await get_user_by_discord_id(
        member.id
    )


    if current is None:

        await interaction.response.send_message(
            f"❌ {member.mention} isn't verified yet. "
            f"To enroll them, provide both "
            f"`new_game_id` and `new_ign`.",
            ephemeral=True
        )


        if new_game_id is None or new_ign is None:

            return


        current = (
            member.id,
            None,
            None,
            0,
            0
        )


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
                f"❌ Ninja Saga ID `{resolved_game_id}` "
                f"is already linked to another Discord account "
                f"(<@{conflict[0]}>, IGN: `{conflict[2]}`).\n\n"
                f"If this is intentional, re-run the command "
                f"with `force: True`.",
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
        f"Ninja Saga ID: `{old_game_id}` → "
        f"`{resolved_game_id}`\n"
        f"IGN: `{old_ign}` → `{resolved_ign}`"
    )




@app_commands.checks.has_permissions(
    administrator=True
)
@bot.tree.command(
    name="delete",
    description="Admin: permanently delete a verification record by Ninja Saga ID"
)
@app_commands.describe(
    game_id="The Ninja Saga User ID of the record to delete"
)
async def delete(
    interaction: discord.Interaction,
    game_id: int
):

    user = await get_user_by_game_id(
        game_id
    )


    if user is None:

        await interaction.response.send_message(
            f"❌ No verification record was found "
            f"for Ninja Saga ID `{game_id}`.",
            ephemeral=True
        )

        return


    discord_id, stored_game_id, ign, missing_checks, removed = user


    deleted = await delete_user_by_game_id(
        game_id
    )


    if deleted is None:

        await interaction.response.send_message(
            f"❌ The verification record for Ninja Saga ID "
            f"`{game_id}` could not be deleted.",
            ephemeral=True
        )

        return


    await interaction.response.send_message(
        f"🗑️ **Verification record deleted successfully.**\n\n"
        f"Ninja Saga ID: `{stored_game_id}`\n"
        f"IGN: `{ign}`\n"
        f"Discord ID: `{discord_id}`\n"
        f"Previous status: "
        f"`{'Removed' if removed else 'Active'}`",
        ephemeral=True
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
