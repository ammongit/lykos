from collections import defaultdict, Counter
from datetime import datetime, timedelta

import threading
import itertools
import random
import time
import math
import re

from src.containers import UserDict, UserSet
from src.decorators import COMMANDS, command, event_listener, handle_error
from src.functions import get_players
from src.warnings import decrement_stasis
from src.messages import messages
from src.events import Event
from src.cats import Wolfchat, All
from src import channels

import botconfig

LAST_START = UserDict() # type: UserDict[users.User, List[datetime, int]]
START_VOTES = UserSet() # type: UserSet[users.User]
RESTART_TRIES = 0 # type: int
MAX_RETRIES = 3 # constant: not a setting

@command("start", phases=("none", "join"))
def start_cmd(var, wrapper, message):
    """Start a game of Werewolf."""
    if wrapper.target is channels.Main:
        start(var, wrapper)

@command("fstart", flag="S", phases=("join",))
def fstart(var, wrapper, message):
    """Force the game to start immediately."""
    channels.Main.send(messages["fstart_success"].format(wrapper.source))
    wrapper.target = channels.Main
    start(var, wrapper, forced=True)

@command("retract", "r", phases=("day", "join"))
def retract(var, wrapper, message):
    """Take back your vote during the day (for whom to lynch)."""
    if wrapper.source not in get_players() or wrapper.source in var.DISCONNECTED:
        return

    with var.GRAVEYARD_LOCK, var.WARNING_LOCK:
        if var.PHASE == "join":
            if wrapper.source not in START_VOTES:
                wrapper.pm(messages["start_novote"])
            else:
                START_VOTES.discard(wrapper.source)
                wrapper.send(messages["start_retract"].format(wrapper.source))

                if not START_VOTES:
                    var.TIMERS["start_votes"][0].cancel()
                    del var.TIMERS["start_votes"]

@event_listener("del_player")
def on_del_player(evt, var, player, all_roles, death_triggers):
    if var.PHASE == "join":
        with var.WARNING_LOCK:
            START_VOTES.discard(player)

            # Cancel the start vote timer if there are no votes left
            if not START_VOTES and "start_votes" in var.TIMERS:
                var.TIMERS["start_votes"][0].cancel()
                del var.TIMERS["start_votes"]


def start(var, wrapper, *, forced=False, restart=""):
    if (not forced and LAST_START and wrapper.source in LAST_START and
            LAST_START[wrapper.source][0] + timedelta(seconds=var.START_RATE_LIMIT) >
            datetime.now() and not restart):
        LAST_START[wrapper.source][1] += 1
        wrapper.source.send(messages["command_ratelimited"])
        return

    if restart:
        global RESTART_TRIES
        RESTART_TRIES += 1
    if RESTART_TRIES > MAX_RETRIES:
        from src.wolfgame import stop_game
        stop_game(var, abort=True)
        return

    if not restart:
        LAST_START[wrapper.source] = [datetime.now(), 1]

    villagers = get_players()
    vils = set(get_players())

    if not restart:
        if var.PHASE == "none":
            wrapper.source.send(messages["no_game_running"].format(botconfig.CMD_CHAR))
            return
        if var.PHASE != "join":
            wrapper.source.send(messages["werewolf_already_running"])
            return
        if wrapper.source not in villagers and not forced:
            return

        now = datetime.now()
        var.GAME_START_TIME = now  # Only used for the idler checker
        dur = int((var.CAN_START_TIME - now).total_seconds())
        if dur > 0 and not forced:
            plural = "" if dur == 1 else "s"
            wrapper.send(messages["please_wait"].format(dur, plural))
            return

        if len(villagers) < var.MIN_PLAYERS:
            wrapper.send(messages["not_enough_players"].format(wrapper.source, var.MIN_PLAYERS))
            return

        if len(villagers) > var.MAX_PLAYERS:
            wrapper.send.send(messages["max_players"].format(wrapper.source, var.MAX_PLAYERS))
            return

        with var.WARNING_LOCK:
            if not forced and wrapper.source in START_VOTES:
                wrapper.pm(messages["start_already_voted"])
                return

            start_votes_required = min(math.ceil(len(villagers) * var.START_VOTES_SCALE), var.START_VOTES_MAX)
            if not forced and len(START_VOTES) < start_votes_required:
                # If there's only one more vote required, start the game immediately.
                # Checked here to make sure that a player that has already voted can't
                # vote again for the final start.
                if len(START_VOTES) < start_votes_required - 1:
                    START_VOTES.add(wrapper.source)
                    remaining_votes = start_votes_required - len(START_VOTES)
                    word = "vote" if remaining_votes == 1 else "votes"
                    wrapper.send(messages["start_voted"].format(wrapper.source, remaining_votes, word))

                    # If this was the first vote
                    if len(START_VOTES) == 1:
                        t = threading.Timer(60, expire_start_votes, (var, wrapper.target))
                        var.TIMERS["start_votes"] = (t, time.time(), 60)
                        t.daemon = True
                        t.start()
                    return

        if not var.FGAMED:
            votes = {} #key = gamemode, not hostmask
            for gamemode in var.GAMEMODE_VOTES.values():
                if len(villagers) >= var.GAME_MODES[gamemode][1] and len(villagers) <= var.GAME_MODES[gamemode][2]:
                    votes[gamemode] = votes.get(gamemode, 0) + 1
            voted = [gamemode for gamemode in votes if votes[gamemode] == max(votes.values()) and votes[gamemode] >= len(villagers)/2]
            if voted:
                from src.wolfgame import cgamemode
                cgamemode(random.choice(voted))
            else:
                possiblegamemodes = []
                numvotes = 0
                for gamemode, num in votes.items():
                    if len(villagers) < var.GAME_MODES[gamemode][1] or len(villagers) > var.GAME_MODES[gamemode][2] or var.GAME_MODES[gamemode][3] == 0:
                        continue
                    possiblegamemodes += [gamemode] * num
                    numvotes += num
                if len(villagers) - numvotes > 0:
                    possiblegamemodes += [None] * ((len(villagers) - numvotes) // 2)
                # check if we go with a voted mode or a random mode
                gamemode = random.choice(possiblegamemodes)
                if gamemode is None:
                    possiblegamemodes = []
                    for gamemode in var.GAME_MODES.keys() - var.DISABLED_GAMEMODES:
                        if len(villagers) >= var.GAME_MODES[gamemode][1] and len(villagers) <= var.GAME_MODES[gamemode][2] and var.GAME_MODES[gamemode][3] > 0:
                            possiblegamemodes += [gamemode] * var.GAME_MODES[gamemode][3]
                    gamemode = random.choice(possiblegamemodes)
                from src.wolfgame import cgamemode
                cgamemode(gamemode)

    else:
        from src.wolfgame import cgamemode
        cgamemode(restart)
        var.GAME_ID = time.time() # restart reaper timer

    from src.wolfgame import chk_win_conditions # TODO: Move that into its own postgame module
    event = Event("role_attribution", {"addroles": Counter()})
    if event.dispatch(var, chk_win_conditions, villagers):
        addroles = event.data["addroles"]
        strip = lambda x: re.sub("\(.*\)", "", x)
        lv = len(villagers)
        roles = []
        for num, rolelist in var.CURRENT_GAMEMODE.ROLE_GUIDE.items():
            if num <= lv:
                roles.extend(rolelist)
        defroles = Counter(strip(x) for x in roles)
        for role, count in list(defroles.items()):
            if role[0] == "-":
                srole = role[1:]
                defroles[srole] -= count
                del defroles[role]
                if defroles[srole] == 0:
                    del defroles[srole]
        if not defroles:
            wrapper.send(messages["no_settings_defined"].format(wrapper.source, lv))
            return
        for role, num in defroles.items():
            addroles[role] = max(addroles.get(role, num), len(var.FORCE_ROLES.get(role, ())))
        if sum([addroles[r] for r in addroles if r not in var.CURRENT_GAMEMODE.SECONDARY_ROLES]) > lv:
            wrapper.send(messages["too_many_roles"])
            return
        for role in All:
            addroles.setdefault(role, 0)
    else:
        addroles = event.data["addroles"]

    # convert roleset aliases into the appropriate roles
    possible_rolesets = [Counter()]
    roleset_roles = defaultdict(int)
    for role, amt in list(addroles.items()):
        # not a roleset? add a fixed amount of them
        if role not in var.CURRENT_GAMEMODE.ROLE_SETS:
            for pr in possible_rolesets:
                pr[role] += amt
            continue
        # if a roleset, ensure we don't try to expose the roleset name in !stats or future attribution
        del addroles[role]
        # init !stats with all 0s so that it can number things properly; the keys need to exist in the Counter
        # across every possible roleset so that !stats works right
        rs = Counter(var.CURRENT_GAMEMODE.ROLE_SETS[role])
        for r in rs:
            for pr in possible_rolesets:
                pr[r] += 0
        toadd = random.sample(list(rs.elements()), amt)
        for r in toadd:
            addroles[r] += 1
            roleset_roles[r] += 1
        add_rolesets = []
        temp_rolesets = []
        for c in itertools.combinations(rs.elements(), amt):
            add_rolesets.append(Counter(c))
        for pr in possible_rolesets:
            for ar in add_rolesets:
                temp = Counter(pr)
                temp.update(ar)
                temp_rolesets.append(temp)
        possible_rolesets = temp_rolesets

    if var.ORIGINAL_SETTINGS and not restart:  # Custom settings
        need_reset = True
        wvs = sum(addroles[r] for r in Wolfchat)
        if len(villagers) < (sum(addroles.values()) - sum(addroles[r] for r in var.CURRENT_GAMEMODE.SECONDARY_ROLES)):
            wrapper.send(messages["too_few_players_custom"])
        elif not wvs and var.CURRENT_GAMEMODE.name != "villagergame":
            wrapper.send(messages["need_one_wolf"])
        elif wvs > (len(villagers) / 2):
            wrapper.send(messages["too_many_wolves"])
        else:
            need_reset = False

        if need_reset:
            from src.wolfgame import reset_settings
            reset_settings()
            wrapper.send(messages["default_reset"].format(botconfig.CMD_CHAR))
            var.PHASE = "join"
            return

    if var.ADMIN_TO_PING is not None and not restart:
        for decor in (COMMANDS["join"] + COMMANDS["start"]):
            decor(_command_disabled)

    var.ROLES.clear()
    var.MAIN_ROLES.clear()
    var.NIGHT_COUNT = 0
    var.DAY_COUNT = 0
    var.TRAITOR_TURNED = False
    var.FINAL_ROLES = {}
    var.EXTRA_WOLVES = 0

    var.DEADCHAT_PLAYERS.clear()
    var.SPECTATING_WOLFCHAT.clear()
    var.SPECTATING_DEADCHAT.clear()

    for role in All:
        var.ROLES[role] = UserSet()
    var.ROLES[var.DEFAULT_ROLE] = UserSet()
    for role, ps in var.FORCE_ROLES.items():
        if role not in var.CURRENT_GAMEMODE.SECONDARY_ROLES.keys():
            vils.difference_update(ps)

    for role, count in addroles.items():
        if role in var.CURRENT_GAMEMODE.SECONDARY_ROLES:
            var.ROLES[role] = (None,) * count
            continue # We deal with those later, see below

        to_add = set()

        if role in var.FORCE_ROLES:
            if len(var.FORCE_ROLES[role]) > count:
                channels.Main.send(messages["error_frole_too_many"].format(role))
                return
            for user in var.FORCE_ROLES[role]:
                # If multiple main roles were forced, only first one is put in MAIN_ROLES
                if not user in var.MAIN_ROLES:
                    var.MAIN_ROLES[user] = role
                var.ORIGINAL_MAIN_ROLES[user] = role
                to_add.add(user)
                count -= 1

        selected = random.sample(vils, count)
        for x in selected:
            var.MAIN_ROLES[x] = role
            var.ORIGINAL_MAIN_ROLES[x] = role
            vils.remove(x)
        var.ROLES[role].update(selected)
        var.ROLES[role].update(to_add)
    var.ROLES[var.DEFAULT_ROLE].update(vils)
    for x in vils:
        var.MAIN_ROLES[x] = var.DEFAULT_ROLE
        var.ORIGINAL_MAIN_ROLES[x] = var.DEFAULT_ROLE
    if vils:
        for pr in possible_rolesets:
            pr[var.DEFAULT_ROLE] += len(vils)

    # Collapse possible_rolesets into var.ROLE_STATS
    # which is a FrozenSet[FrozenSet[Tuple[str, int]]]
    possible_rolesets_set = set()
    event = Event("reconfigure_stats", {"new": []})
    for pr in possible_rolesets:
        event.data["new"] = [pr]
        event.dispatch(var, pr, "start")
        for v in event.data["new"]:
            if min(v.values()) >= 0:
                possible_rolesets_set.add(frozenset(v.items()))
    var.ROLE_STATS = frozenset(possible_rolesets_set)

    # Now for the secondary roles
    for role, dfn in var.CURRENT_GAMEMODE.SECONDARY_ROLES.items():
        count = len(var.ROLES[role])
        var.ROLES[role] = UserSet()
        if role in var.FORCE_ROLES:
            ps = var.FORCE_ROLES[role]
            var.ROLES[role].update(ps)
            count -= len(ps)
        # Don't do anything further if this secondary role was forced on enough players already
        if count <= 0:
            continue
        possible = get_players(dfn)
        if len(possible) < count:
            wrapper.send(messages["not_enough_targets"].format(role))
            if var.ORIGINAL_SETTINGS:
                from src.wolfgame import reset_settings
                var.ROLES.clear()
                var.ROLES["person"] = UserSet(var.ALL_PLAYERS)
                reset_settings()
                wrapper.send(messages["default_reset"].format(botconfig.CMD_CHAR))
                var.PHASE = "join"
                return
            else:
                wrapper.send(messages["role_skipped"])
                continue
        var.ROLES[role].update(x for x in random.sample(possible, count))

    with var.WARNING_LOCK: # cancel timers
        for name in ("join", "join_pinger", "start_votes"):
            if name in var.TIMERS:
                var.TIMERS[name][0].cancel()
                del var.TIMERS[name]

    var.LAST_STATS = None
    var.LAST_TIME = None

    for role, players in var.ROLES.items():
        for player in players:
            evt = Event("new_role", {"messages": [], "role": role, "in_wolfchat": False}, inherit_from=None)
            evt.dispatch(var, player, None)

    if not restart:
        gamemode = var.CURRENT_GAMEMODE.name
        if gamemode == "villagergame":
            gamemode = "default"

        # Alert the players to option changes they may not be aware of
        options = []
        if var.ORIGINAL_SETTINGS.get("ROLE_REVEAL") is not None:
            if var.ROLE_REVEAL == "on":
                options.append("role reveal")
            elif var.ROLE_REVEAL == "team":
                options.append("team reveal")
            elif var.ROLE_REVEAL == "off":
                options.append("no role reveal")
        if var.ORIGINAL_SETTINGS.get("STATS_TYPE") is not None:
            if var.STATS_TYPE == "disabled":
                options.append("no stats")
            else:
                options.append("{0} stats".format(var.STATS_TYPE))
        if var.ORIGINAL_SETTINGS.get("ABSTAIN_ENABLED") is not None or var.ORIGINAL_SETTINGS.get("LIMIT_ABSTAIN") is not None:
            if var.ABSTAIN_ENABLED and var.LIMIT_ABSTAIN:
                options.append("restricted abstaining")
            elif var.ABSTAIN_ENABLED:
                options.append("unrestricted abstaining")
            else:
                options.append("no abstaining")

        if len(options) > 2:
            options = " with {0}, and {1}".format(", ".join(options[:-1]), options[-1])
        elif len(options) == 2:
            options = " with {0} and {1}".format(options[0], options[1])
        elif len(options) == 1:
            options = " with {0}".format(options[0])
        else:
            options = ""

        wrapper.send(messages["welcome"].format(", ".join(x.nick for x in villagers), gamemode, options))
        wrapper.target.mode("+m")

    var.ORIGINAL_ROLES.clear()
    for role, players in var.ROLES.items():
        var.ORIGINAL_ROLES[role] = players.copy()

    var.DAY_TIMEDELTA = timedelta(0)
    var.NIGHT_TIMEDELTA = timedelta(0)
    var.DAY_START_TIME = datetime.now()
    var.NIGHT_START_TIME = datetime.now()
    var.LAST_PING = None

    var.PLAYERS = {plr:dict(var.USERS[plr.nick]) for plr in villagers if plr.nick in var.USERS} # FIXME: Please kill this

    if restart:
        var.PHASE = "join" # allow transition_* to run properly if game was restarted on first night
    if not var.START_WITH_DAY:
        from src.wolfgame import transition_night
        var.GAMEPHASE = "night"
        transition_night()
    else:
        from src.wolfgame import transition_day
        var.FIRST_DAY = True
        var.GAMEPHASE = "day"
        transition_day()

    decrement_stasis()

    if not (botconfig.DEBUG_MODE and var.DISABLE_DEBUG_MODE_REAPER):
        # DEATH TO IDLERS!
        from src.wolfgame import reaper
        reapertimer = threading.Thread(None, reaper, args=(wrapper.client, var.GAME_ID))
        reapertimer.daemon = True
        reapertimer.start()

def _command_disabled(var, wrapper, message):
    wrapper.send(messages["command_disabled_admin"])

@handle_error
def expire_start_votes(var, channel):
    # Should never happen as the timer is removed on game start, but just to be safe
    if var.PHASE != "join":
        return

    with var.WARNING_LOCK:
        START_VOTES.clear()
        channel.send(messages["start_expired"])

@event_listener("reset")
def on_reset(evt, var):
    global MAX_RETRIES
    LAST_START.clear()
    START_VOTES.clear()
    MAX_RETRIES = 0
