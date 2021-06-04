import asyncio
import datetime
import gettext
import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from difflib import get_close_matches
from itertools import chain
from pathlib import Path

import asyncpg
import discord
from discord.ext import commands, menus
from dotenv import load_dotenv

#Language
lang = "en"
#Is this build experimental? Enable for additional debugging. Also writes to a different database to prevent conflict issues.
EXPERIMENTAL = False
#Version of the bot
current_version = "4.4.0"
#Loading token from .env file. If this file does not exist, nothing will work.
load_dotenv()
TOKEN = os.getenv("TOKEN")
DBPASS = os.getenv("DBPASS")

'''
All extensions that are loaded on boot-up, change these to alter what modules you want (Note: These refer to filenames NOT cognames)
Note: Without the extension admin_commands, most things will break, so I consider this a must-have. Remove at your own peril.
Note #2: If you remove the extension "help", then the bot will fall back to the default help command.
Jishaku is a bot-owner only debug extension, requires 'pip install jishaku'.
'''
initial_extensions = (
    'extensions.help',
    'extensions.admin_commands', 
    'extensions.moderation',
    'extensions.reaction_roles', 
    'extensions.ktp', 
    'extensions.matchmaking', 
    'extensions.tags', 
    'extensions.setup', 
    'extensions.userlog', 
    'extensions.timers', 
    'extensions.fun', 
    'extensions.annoverse',
    'extensions.giveaway',
    'extensions.misc_commands',
    'jishaku'
)


async def get_prefix(bot, message):
    '''
    Gets custom prefix for the current guild
    '''
    if message.guild is None:
        return bot.DEFAULT_PREFIX
    elif message.guild.id in bot.cache['prefix']: #If prefix is cached
        return bot.cache['prefix'][message.guild.id] #Get from cache
    else:
        async with bot.pool.acquire() as con: #Else try to find in db
            results = await con.fetch('''SELECT prefix FROM global_config WHERE guild_id = $1''', message.guild.id)
            if len(results) !=0 and results[0] and results[0].get('prefix'):
                prefixes = results[0].get('prefix')
                bot.cache['prefix'][message.guild.id] = prefixes
                return prefixes
            else: #Fallback to default prefix if there is none found
                bot.cache['prefix'][message.guild.id] = bot.DEFAULT_PREFIX #Cache it
                return bot.DEFAULT_PREFIX


class SnedBot(commands.Bot):

    def __init__(self):
        allowed_mentions = discord.AllowedMentions(everyone=False, users=True, roles=True, replied_user=True)
        activity = discord.Activity(name='@Sned', type=discord.ActivityType.listening)
        #Disabled: presences, typing, integrations, webhooks, voice_states
        intents = discord.Intents(
            guilds = True,
            members = True,
            bans = True,
            emojis = True,
            messages = True,
            invites = True,
            reactions = True
        )
        super().__init__(command_prefix=get_prefix, allowed_mentions=allowed_mentions, 
        intents=intents, case_insensitive=True, activity=activity, max_messages=10000)

        self.EXPERIMENTAL = EXPERIMENTAL

        self.DEFAULT_PREFIX = 'sn '
        if self.EXPERIMENTAL == True :
            self.DEFAULT_PREFIX = '?'
            logging.basicConfig(level=logging.INFO)
            DB_NAME = "sned_exp"
        else :
            logging.basicConfig(level=logging.INFO)
            DB_NAME = "sned"

        self.BASE_DIR = os.path.dirname(os.path.abspath(__file__))

        self.lang = lang
        self.pool = self.loop.run_until_complete(asyncpg.create_pool(dsn="postgres://postgres:{DBPASS}@192.168.1.101:5432/{db_name}".format(DBPASS=DBPASS, db_name=DB_NAME)))
        self.whitelisted_guilds = [372128553031958529, 627876365223591976, 818223666143690783, 836248845268680785]
        self.anno_guilds = (372128553031958529, 627876365223591976, 818223666143690783) #Guilds whitelisted for Anno-related commands
        self.cache = {}
        self.cache['prefix'] = {}
        self.cmd_cd_mapping = commands.CooldownMapping.from_cooldown(10, 10, commands.BucketType.channel)
        self.current_version = current_version

        self.loop.create_task(self.startup())

    
    async def on_ready(self):
        logging.info("Connected to Discord!")


    async def startup(self):
        '''
        Gets executed on first start of the bot, sets up the prefix cache
        '''
        await self.wait_until_ready()

        logging.info("Initialized as {0.user}".format(self))
        if self.EXPERIMENTAL == True :
            logging.warning("Experimental mode is enabled.")
            cogs = await self.current_cogs()
            logging.info(f"Cogs loaded: {cogs}")
        #Insert all guilds the bot is member of into the db global config on startup
        async with self.pool.acquire() as con:
            for guild in self.guilds:
                await con.execute('''
                INSERT INTO global_config (guild_id) VALUES ($1)
                ON CONFLICT (guild_id) DO NOTHING''', guild.id)
            results = await con.fetch('''SELECT * FROM global_config''')
            logging.info("Initializing cache...")
            for result in results:
                if result.get('prefix'):
                    self.cache['prefix'][result.get('guild_id')] = result.get('prefix')
                else:
                    self.cache['prefix'][result.get('guild_id')] = self.DEFAULT_PREFIX
            logging.info("Cache ready!")


    def get_localization(self, extension_name:str, lang:str):
        '''
        Installs the proper localization for a given extension
        '''
        LOCALE_PATH = Path(self.BASE_DIR, 'locale')

        if lang == "de":
            de = gettext.translation('main', localedir=LOCALE_PATH, languages=['de'])
            de.install()
            _ = de.gettext
            return _
        #Fallback to English
        else :
            lang = "en"
            _ = gettext.gettext
            return _

    
    async def current_cogs(self):
        '''
        Simple function that just gets all currently loaded cog/extension names
        '''
        cogs = []
        for cogName,cogClass in bot.cogs.items(): # pylint: disable=<unused-variable>
            cogs.append(cogName)
        return cogs

    async def on_message(self, message):
        bucket = self.cmd_cd_mapping.get_bucket(message)
        retry_after = bucket.update_rate_limit()
        if not retry_after: #If not ratelimited
            mentions = [f"<@{bot.user.id}>", f"<@!{bot.user.id}>"]
            if mentions[0] == message.content or mentions[1] == message.content:
                async with self.pool.acquire() as con:
                    results = await con.fetch('''SELECT prefix FROM global_config WHERE guild_id = $1''', message.guild.id)
                if results[0].get('prefix'):
                    prefix = results[0].get('prefix')
                else:
                    prefix = [self.DEFAULT_PREFIX]
                embed=discord.Embed(title=_("Beep Boop!"), description=_("My prefixes on this server are the following: `{prefix}`").format(prefix=", ".join(prefix)), color=0xfec01d)
                embed.set_thumbnail(url=self.user.avatar_url)
                await message.reply(embed=embed)

            await self.process_commands(message)
        else:
            pass #Ignore requests that would exceed rate-limits

    async def on_command(self, ctx):
        logging.info(f"{ctx.author} called command {ctx.message.content} in guild {ctx.guild.id}")


    async def on_guild_join(self, guild):
        if guild.id == 336642139381301249: #Discord.py specific join behaviour
            async with bot.pool.acquire() as con:
                await con.execute('INSERT INTO global_config (guild_id) VALUES ($1)', guild.id)
                await con.execute('''UPDATE global_config SET prefix = array_append(prefix,$1) WHERE guild_id = $2''', "sned ", guild.id)
                logging.info("Joined discord.py! :verycool:")
                return
        #Generate guild entry for DB
        async with bot.pool.acquire() as con:
            await con.execute('INSERT INTO global_config (guild_id) VALUES ($1)', guild.id)
        if guild.system_channel != None :
            try:
                embed=discord.Embed(title=_("Beep Boop!"), description=_("I have been summoned to this server. Use `{prefix}help` to see what I can do!").format(prefix=bot.DEFAULT_PREFIX), color=0xfec01d)
                embed.set_thumbnail(url=self.user.avatar_url)
                await guild.system_channel.send(embed=embed)
            except discord.Forbidden:
                pass
        logging.info(f"Bot has been added to new guild {guild.id}.")


    async def on_guild_remove(self, guild):
        '''
        Erase all settings for this guild on removal to keep the db tidy.
        The reason this does not use GlobalConfig.deletedata() is to not recreate the entry for the guild
        '''
        async with bot.pool.acquire() as con:
            await con.execute('''DELETE FROM global_config WHERE guild_id = $1''', guild.id)
        logging.info(f"Bot has been removed from guild {guild.id}, correlating data erased.")


    async def on_command_error(self, ctx, error):
        '''
        Global Error Handler

        Generic error handling. Will catch all otherwise not handled errors
        '''

        if isinstance(error, commands.CheckFailure):
            logging.info(f"{ctx.author} tried calling a command but did not meet checks.")
            if isinstance(error, commands.BotMissingPermissions):
                embed=discord.Embed(title="❌ " + _("Bot missing permissions"), description=_("The bot requires additional permissions to execute this command.\n**Error:**```{error}```").format(error=error), color=self.errorColor)
                return await ctx.send(embed=embed)

        if isinstance(error, commands.CommandInvokeError):
            if isinstance(error.original, asyncio.exceptions.TimeoutError):
                embed=discord.Embed(title=self.errorTimeoutTitle, description=self.errorTimeoutDesc, color=self.errorColor)
                embed.set_footer(text=self.requestFooter.format(user_name=ctx.author.name, discrim=ctx.author.discriminator), icon_url=ctx.author.avatar_url)
                return await ctx.send(embed=embed)
            else:
                raise error

        elif isinstance(error, commands.CommandNotFound):
            '''
            This is a fancy suggestion thing that will suggest commands that are similar in case of typos.
            '''
            logging.info(f"{ctx.author} tried calling a command in but the command was not found. ({ctx.message.content})")
            
            cmd = ctx.invoked_with.lower()

            cmds = [cmd.name for cmd in bot.commands if not cmd.hidden]
            allAliases = [cmd.aliases for cmd in bot.commands if not cmd.hidden]
            aliases = list(chain(*allAliases))

            matches = get_close_matches(cmd, cmds)
            aliasmatches = get_close_matches(cmd, aliases)

            if len(matches) > 0:
                embed=discord.Embed(title=self.unknownCMDstr, description=_("Did you mean `{prefix}{match}`?").format(prefix=ctx.prefix, match=matches[0]), color=self.unknownColor)
                embed.set_footer(text=bot.requestFooter.format(user_name=ctx.author.name, discrim=ctx.author.discriminator), icon_url=ctx.author.avatar_url)
                return await ctx.send(embed=embed)
            elif len(aliasmatches) > 0:
                embed=discord.Embed(title=self.unknownCMDstr, description=_("Did you mean `{prefix}{match}`?").format(prefix=ctx.prefix, match=aliasmatches[0]), color=self.unknownColor)
                embed.set_footer(text=self.requestFooter.format(user_name=ctx.author.name, discrim=ctx.author.discriminator), icon_url=ctx.author.avatar_url)
                return await ctx.send(embed=embed)
            '''else:
                embed=discord.Embed(title=bot.unknownCMDstr, description=_("Use `{prefix}help` for a list of available commands.").format(prefix=ctx.prefix), color=bot.unknownColor)
                embed.set_footer(text=bot.requestFooter.format(user_name=ctx.author.name, discrim=ctx.author.discriminator), icon_url=ctx.author.avatar_url)
                await ctx.send(embed=embed)'''

        elif isinstance(error, commands.CommandOnCooldown):
            embed=discord.Embed(title=self.errorCooldownTitle, description=_("Please retry in: `{cooldown}`").format(cooldown=datetime.timedelta(seconds=round(error.retry_after))), color=self.errorColor)
            embed.set_footer(text=bot.requestFooter.format(user_name=ctx.author.name, discrim=ctx.author.discriminator), icon_url=ctx.author.avatar_url)
            return await ctx.send(embed=embed)

        elif isinstance(error, commands.MissingRequiredArgument):
            embed=discord.Embed(title="❌" + _("Missing argument."), description=_("One or more arguments are missing. \n__Hint:__ You can use `{prefix}help {command_name}` to view command usage.").format(prefix=ctx.prefix, command_name=ctx.command.name), color=self.errorColor)
            embed.set_footer(text=self.requestFooter.format(user_name=ctx.author.name, discrim=ctx.author.discriminator), icon_url=ctx.author.avatar_url)
            logging.info(f"{ctx.author} tried calling a command ({ctx.message.content}) but did not supply sufficient arguments.")
            return await ctx.send(embed=embed)


        elif isinstance(error, commands.MaxConcurrencyReached):
            embed = discord.Embed(title=self.errorMaxConcurrencyReachedTitle, description=self.errorMaxConcurrencyReachedDesc, color=self.errorColor)
            embed.set_footer(text=self.requestFooter.format(user_name=ctx.author.name, discrim=ctx.author.discriminator), icon_url=ctx.author.avatar_url)
            return await ctx.channel.send(embed=embed)

        elif isinstance(error, commands.MemberNotFound):
            embed=discord.Embed(title="❌ " + _("Cannot find user by that name"), description=_("Please check if you typed everything correctly, then try again.\n**Error:**```{error}```").format(error=str(error)), color=self.errorColor)
            return await ctx.send(embed=embed)

        elif isinstance(error, commands.errors.BadArgument):
            embed=discord.Embed(title="❌ " + _("Bad argument"), description=_("Invalid data entered! Check `{prefix}help {command_name}` for more information.\n**Error:**```{error}```").format(prefix=ctx.prefix, command_name=ctx.command.name, error=error), color=self.errorColor)
            return await ctx.send(embed=embed)

        elif isinstance(error, commands.TooManyArguments):
            embed=discord.Embed(title="❌ " + _("Too many arguments"), description=_("You have provided more arguments than what `{prefix}{command_name}` can take. Check `{prefix}help {command_name}` for more information.").format(prefix=ctx.prefix, command_name=ctx.command.name), color=self.errorColor)
            return await ctx.send(embed=embed)

        else :
            #If no known error has been passed, we will print the exception to console as usual
            #IMPORTANT!!! If you remove this, your command errors will not get output to console.
            print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
            traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)


bot = SnedBot()
_ = bot.get_localization('main', lang)


'''
#Error/warn messages

This contains strings for common error/warn msgs.
'''
#Errors:
bot.errorColor = 0xff0000
bot.errorTimeoutTitle = "🕘 " + _("Error: Timed out")
bot.errorTimeoutDesc = _("Your session has expired. Execute the command again!")
bot.errorDataTitle = "❌ " + _("Error: Invalid data entered")
bot.errorDataDesc = _("Operation cancelled.")
bot.errorEmojiTitle = "❌ " + _("Error: Invalid reaction entered")
bot.errorEmojiDesc = _("Operation cancelled.")
bot.errorFormatTitle = "❌ " + _("Error: Invalid format entered")
bot.errorFormatDesc = _("Operation cancelled.")
bot.errorCheckFailTitle = "❌ " + _("Error: Insufficient permissions")
bot.errorCheckFailDesc = _("You did not meet the checks to execute this command. This could also be caused by incorrect configuration. \nType `{prefix}help` for a list of available commands.")
bot.errorCooldownTitle = "🕘 " + _("Error: This command is on cooldown")
bot.errorMissingModuleTitle = "❌ " + _("Error: Missing module")
bot.errorMissingModuleDesc = _("This operation is missing a module")
bot.errorMaxConcurrencyReachedTitle = "❌ " + _("Error: Max concurrency reached!")
bot.errorMaxConcurrencyReachedDesc= _("You have reached the maximum amount of instances for this command.")
#Warns:
bot.warnColor = 0xffcc4d
bot.warnDataTitle = "⚠️ " + _("Warning: Invalid data entered")
bot.warnDataDesc = _("Please check command usage.")
bot.warnEmojiTitle = "⚠️ " + _("Warning: Invalid reaction entered")
bot.warnEmojiDesc = _("Please enter a valid reaction.")
bot.warnFormatTitle = "⚠️ " + _("Warning: Invalid format entered")
bot.warnFormatDesc = _("Please try entering valid data.")
bot.requestFooter = _("Requested by {user_name}#{discrim}")
bot.unknownCMDstr = "❓ " + _("Unknown command!")
#Misc:
bot.embedBlue = 0x009dff
bot.embedGreen = 0x77b255
bot.unknownColor = 0xbe1931
bot.miscColor = 0xc2c2c2


class GlobalConfig():
    '''
    Class that handles the global configuration & users within the database
    These tables are created automatically as they must exist
    '''

    @dataclass
    class User:
        '''
        Represents a user stored inside the database
        '''
        user_id:int
        guild_id:int
        flags:list=None
        warns:int=0
        is_muted:bool=False
        notes:str=None

    def __init__(self, bot):
        async def init_table():
            self.bot = bot
            async with bot.pool.acquire() as con:
                await con.execute('''
                CREATE TABLE IF NOT EXISTS public.global_config
                (
                    guild_id bigint NOT NULL,
                    prefix text[],
                    PRIMARY KEY (guild_id)
                )''')
                await con.execute('''
                CREATE TABLE IF NOT EXISTS public.users
                (
                    user_id bigint NOT NULL,
                    guild_id bigint NOT NULL,
                    flags text[],
                    warns integer NOT NULL DEFAULT 0,
                    is_muted bool NOT NULL DEFAULT false,
                    notes text,
                    PRIMARY KEY (user_id, guild_id),
                    FOREIGN KEY (guild_id)
                        REFERENCES global_config (guild_id)
                        ON DELETE CASCADE
                )''')
        bot.loop.run_until_complete(init_table())


    async def deletedata(self, guild_id):
        '''
        Deletes all data related to a specific guild, including but not limited to: all settings, priviliged roles, stored tags, stored multiplayer listings etc...
        Warning! This also erases any stored warnings & other moderation actions for the guild!
        '''
        #The nuclear option c:
        async with self.bot.pool.acquire() as con:
            await con.execute('''DELETE FROM global_config WHERE guild_id = $1''', guild_id)
            #This one is necessary so that the list of guilds the bot is in stays accurate
            await con.execute('''INSERT INTO global_config (guild_id) VALUES ($1)''', guild_id)
        logging.warning(f"Config reset for guild {guild_id}.")
    

    async def update_user(self, user):
        '''
        Takes an instance of GlobalConfig.User and tries to either update or create a new user entry if one does not exist already
        '''
        async with bot.pool.acquire() as con:
            try:
                await con.execute('''
                INSERT INTO users (user_id, guild_id, flags, warns, is_muted, notes) 
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (user_id, guild_id) DO
                UPDATE SET flags = $3, warns = $4, is_muted = $5, notes = $6''', user.user_id, user.guild_id, user.flags, user.warns, user.is_muted, user.notes)
            except asyncpg.exceptions.ForeignKeyViolationError:
                logging.warn('Trying to update a guild db_user whose guild no longer exists. This could be due to pending timers.')

    async def get_user(self, user_id, guild_id): 
        '''
        Gets an instance of GlobalConfig.User that contains basic information about the user in relation to a guild
        Returns None if not found
        '''
        async with bot.pool.acquire() as con:
            result = await con.fetch('''SELECT * FROM users WHERE user_id = $1 AND guild_id = $2''', user_id, guild_id)
        if result:
            user = self.User(user_id = result[0].get('user_id'), guild_id=result[0].get('guild_id'), flags=result[0].get('flags'), 
            warns=result[0].get('warns'), is_muted=result[0].get('is_muted'), notes=result[0].get('notes'))
            return user
        else:
            user = self.User(user_id = user_id, guild_id = guild_id) #Generate a new db user if none exists
            await self.update_user(user) 
            return user

    
    async def get_all_guild_users(self, guild_id):
        '''
        Returns all users related to a specific guild as a list of GlobalConfig.User
        Return None if no users are contained in the database
        '''
        async with bot.pool.acquire() as con:
            results = await con.fetch('''SELECT * FROM users WHERE guild_id = $1''', guild_id)
        if results:
            users = []
            for result in results:
                user = self.User(user_id = result.get('user_id'), guild_id=result.get('guild_id'), flags=result.get('flags'), 
                warns=result.get('warns'), is_muted=result.get('is_muted'), notes=result.get('notes'))
                users.append(user)
            return users

bot.global_config = GlobalConfig(bot)

'''
Loading extensions, has to be AFTER global_config is initialized so global_config already exists
'''

if __name__ == '__main__':
    '''
    Loading extensions from the list of extensions defined in initial_extensions
    '''
    for extension in initial_extensions:
        try:
            bot.load_extension(extension)
        except Exception as e:
            logging.error(f'Failed to load extension {extension}.', file=sys.stderr)
            traceback.print_exc()


class CustomChecks():
    '''
    Custom checks for commands across the bot
    '''

    async def has_owner(self, ctx):
        '''
        True if the invoker is either bot or guild owner
        '''
        if ctx.guild:
            return ctx.author.id == ctx.bot.owner_id or ctx.author.id == ctx.guild.owner_id
        else:
            return ctx.author.id == ctx.bot.owner_id


    async def has_priviliged(self, ctx):
        '''
        True if invoker is either bot owner, guild owner, administrator, 
        or has a specified priviliged role
        '''
        if ctx.guild:
            userRoles = [x.id for x in ctx.author.roles]
            async with bot.pool.acquire() as con:
                results = await con.fetch('''SELECT priviliged_role_id FROM priviliged WHERE guild_id = $1''', ctx.guild.id)
                privroles = [result.get('priviliged_role_id') for result in results]
                return any(role in userRoles for role in privroles) or (ctx.author.id == ctx.bot.owner_id or ctx.author.id == ctx.guild.owner_id or ctx.author.guild_permissions.administrator)


bot.custom_checks = CustomChecks()

#Run bot with token from .env
try :
    bot.run(TOKEN)
except KeyboardInterrupt :
    bot.loop.run_until_complete(bot.pool.close())
    bot.close()
