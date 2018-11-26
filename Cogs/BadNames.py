import re
from concurrent import futures

import discord
from discord.ext import commands

from Util import Configuration, Utils


class BadNames:

    def __init__(self, bot):
        self.bot: commands.Bot = bot
        self.detectors = dict()
        self.name_messages = dict()
        self.handled = set()
        self.actions = {
            "🚪": self.ban,
            "👢": self.kick,
            "🗑": self.clean_nick,
            "📝": self.rename
        }

        for guild in bot.guilds:
            self.assemble_detector(guild)

    def assemble_detector(self, guild):
        bad_names = Configuration.get_var(guild.id, "BAD_NAMES")
        if len(bad_names) > 0:
            capture = "|".join(bad_names)
            self.detectors[guild.id] = re.compile(f"({capture})", flags=re.IGNORECASE)

    async def on_member_update(self, before, after):
        if after.id in self.handled:
            self.handled.remove(after.id)
            return
        if before.nick != after.nick or before.name != after.name:
            name_matches = self.get_matches_pretty(after.guild.id, after.name)
            if after.nick is not None:
                nick_matches = self.get_matches_pretty(after.guild.id, after.nick)
            message = ""
            if len(name_matches) > 0:
                message = f"Spotted {name_matches[0]}#{after.discriminator} (``{after.id}``) with a bad username"
                if after.nick is not None:
                    if len(nick_matches) > 0:
                        message += f" AND a bad nickname ({nick_matches[0]})"
                    else:
                        message += f" (current nickname is {after.nick})"
                if len(name_matches) >= 2 or (after.nick is not None and len(nick_matches) >= 2):
                    if after.nick is not None:
                        name_matches.extend(nick_matches)
                    out = '\n'.join(name_matches)
                    message += f"\nAll matches: \n{out}"
                message += "\nWhat do you want me to do?"

            elif after.nick is not None and len(nick_matches) > 0:
                message = f"Spotted {str(after)} (``{after.id}``) with a bad nickname ({nick_matches[0]})"
                if len(nick_matches) > 0:
                    out = '\n'.join(nick_matches)
                    message += f"\nAll matches: {out}"
                message += "\nWhat do you want me to do?"
            channel = self.bot.get_channel(Configuration.get_var(after.guild.id, "ACTION_CHANNEL"))
            if channel is not None:
                message = await channel.send(message)
                self.name_messages[message.id] = after.id
                await Utils.add_reactions(message, self.actions)

                # remove the oldest
                if len(self.name_messages) > 50:
                    del self.name_messages[sorted(self.name_messages.keys())[0]]


    def get_matches(self, guild_id, name):
        return self.detectors[guild_id].findall(name) if guild_id in self.detectors else []

    def get_matches_pretty(self, guild_id, name, matches = None):
        return [name.replace(match, f"**{match}**") for match in (self.get_matches(guild_id, name) if matches is None else matches)]

    @commands.group()
    async def blacklist(self, ctx):
        # TODO: show help instead
        pass

    @blacklist.command()
    async def show(self, ctx):
        bad_names = Configuration.get_var(ctx.guild.id, "BAD_NAMES")
        pages = Utils.paginate('\n'.join(bad_names), prefix="Blacklist entries (part {page}/{pages}):```\n",
                               suffix="\n```")
        # TODO: confirmation if there are too many entries? Not sure we'll ever 100+ entries
        for page in pages:
            await ctx.send(page)

    @blacklist.command("add")
    async def blacklist_add(self, ctx, *, entry: str):
        guild_id = ctx.guild.id
        existing_matches = self.get_matches_pretty(guild_id, entry)
        if len(existing_matches) > 0:
            out = '\n'.join(existing_matches)
            await ctx.send(f"This name is already covered with existing entries: \n{out}")
        else:
            blacklist = Configuration.get_var(guild_id, "BAD_NAMES")
            blacklist.append(entry)
            Configuration.save(guild_id)
            self.assemble_detector(ctx.guild)
            await ctx.send(f"``{entry}`` has been added to the blacklist")

    @blacklist.command("remove")
    async def blacklist_remove(self, ctx, *, entry: str):
        guild_id = ctx.guild.id
        blacklist = Configuration.get_var(guild_id, "BAD_NAMES")
        if entry in blacklist:
            blacklist.remove(entry)
            Configuration.save(guild_id)
            self.assemble_detector(ctx.guild)
            await ctx.send(f"``{entry}`` has been removed from the blacklist")
        else:
            # check if it's matched under something else
            matches = self.get_matches_pretty(guild_id, entry)
            if len(matches) > 0:
                out = '\n'.join(matches)
                await ctx.send(f"``{entry}`` is not on the blacklist by itself, but parts of it are:\n{out}")
            else:
                await ctx.send(f"``{entry}`` is not on the blacklist, nor does it contain anything that is on the list")

    @blacklist.command("check")
    async def blacklist_check(self, ctx, *, entry: str):
        guild_id = ctx.guild.id
        matches = self.get_matches_pretty(guild_id, entry)
        if len(matches) > 0:
            out = '\n'.join(matches)
            await ctx.send(f"Yup, that is blacklisted:\n{out}")
        else:
            await ctx.send(f"``{entry}`` is not blacklisted")

    async def ban(self, channel, user, message_id, mod):
        """Ban the user"""
        try:
            await channel.guild.ban(discord.Object(user), reason=f"Inaproprate name, received ban order from {mod}")
        except discord.HTTPException as ex:
            await channel.send(f"Failed to ban ``{user}``: {ex.text}")
        else:
            await channel.send(f"Banned user ``{user}`` as requested")
            del self.name_messages[message_id]

    async def kick(self, channel, user, message_id, mod):
        """Kick the user"""
        try:
            await channel.guild.kick(discord.Object(user), reason=f"Inaproprate name, received kick order from {mod}")
        except discord.HTTPException as ex:
            await channel.send(f"Failed to kick ``{user}``: {ex.text}")
        else:
            await channel.send(f"Kicked user ``{user}`` as requested")
            del self.name_messages[message_id]

    async def clean_nick(self, channel, user, message_id, mod):
        """Remove nickname"""
        member = channel.guild.get_member(user)
        if member is None:
            await channel.send("This user is no longer on the server")
        else:
            self.handled.add(user)
            await member.edit(nick=None)
            await channel.send("Nickname has been removed")

    async def rename(self, channel, user, message_id, mod):
        """Set a new nickname for the user"""
        member = channel.guild.get_member(user)
        if member is None:
            await channel.send("This user is no longer on the server")
        else:
            await channel.send("Please enter a new nickname for the user:")
            try:
                message = await self.bot.wait_for("on_message", check=lambda m: m.author == mod, timeout=30)
            except futures.TimeoutError:
                await channel.send("No new nickname recieved, canceling")
            else:
                try:
                    self.handled.add(user)
                    await member.edit(nick=message.content)
                except discord.HTTPException as ex:
                    self.handled.remove(user)
                    await channel.send(f"Failed to set that nickname: {ex.text}")
                else:
                    await channel.send("Nickname set!")


    async def on_reaction_add(self, reaction, user):
        if reaction.message.id in self.name_messages and user.id != self.bot.user.id and reaction.emoji in self.actions:
            await self.actions[reaction.emoji](reaction.message.channel, self.name_messages[reaction.message.id], reaction.message.id, user)

def setup(bot):
    bot.add_cog(BadNames(bot))
