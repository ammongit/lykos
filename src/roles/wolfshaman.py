import re
import random
import itertools
from collections import defaultdict, deque

from src.utilities import *
from src import debuglog, errlog, plog, users, channels
from src.functions import get_players, get_all_players, get_main_role, get_reveal_role, get_target
from src.decorators import command, event_listener
from src.containers import UserList, UserSet, UserDict, DefaultUserDict
from src.dispatcher import MessageDispatcher
from src.messages import messages
from src.status import try_misdirection, try_exchange

from src.roles.helper.shamans import get_totem_target, give_totem, setup_variables
from src.roles.helper.wolves import register_killer

TOTEMS, LASTGIVEN, SHAMANS = setup_variables("wolf shaman", knows_totem=True)

register_killer("wolf shaman")

@command("give", "totem", chan=False, pm=True, playing=True, silenced=True, phases=("night",), roles=("wolf shaman",))
def wolf_shaman_totem(var, wrapper, message):
    """Give a totem to a player."""

    target = get_totem_target(var, wrapper, message, LASTGIVEN)
    if not target:
        return

    SHAMANS[wrapper.source] = give_totem(var, wrapper, target, prefix="You", role="wolf shaman", msg=" of {0}".format(TOTEMS[wrapper.source]))

    relay_wolfchat_command(wrapper.client, wrapper.source.nick, messages["shaman_wolfchat"].format(wrapper.source, target), ("wolf shaman",), is_wolf_command=True)

@event_listener("transition_day_begin", priority=4)
def on_transition_day_begin(evt, var):
    # Select random totem recipients if shamans didn't act
    pl = get_players()
    for shaman in get_players(("wolf shaman",)):
        if shaman not in SHAMANS and shaman.nick not in var.SILENCED:
            ps = pl[:]
            if shaman in LASTGIVEN:
                if LASTGIVEN[shaman] in ps:
                    ps.remove(LASTGIVEN[shaman])
            if ps:
                target = random.choice(ps)
                dispatcher = MessageDispatcher(shaman, shaman)

                SHAMANS[shaman] = give_totem(var, dispatcher, target, prefix=messages["random_totem_prefix"], role="wolf shaman", msg=" of {0}".format(TOTEMS[shaman]))
                relay_wolfchat_command(shaman.client, shaman.nick, messages["shaman_wolfchat"].format(shaman, target), ("wolf shaman",), is_wolf_command=True)
            else:
                LASTGIVEN[shaman] = None
        elif shaman not in SHAMANS:
            LASTGIVEN[shaman] = None

@event_listener("transition_night_end", priority=2.01)
def on_transition_night_end(evt, var):
    chances = var.CURRENT_GAMEMODE.TOTEM_CHANCES
    max_totems = sum(x["wolf shaman"] for x in chances.values())
    ps = get_players()
    shamans = get_players(("wolf shaman",))
    for s in list(LASTGIVEN):
        if s not in shamans:
            del LASTGIVEN[s]

    for shaman in shamans:
        pl = ps[:]
        random.shuffle(pl)
        if LASTGIVEN.get(shaman):
            if LASTGIVEN[shaman] in pl:
                pl.remove(LASTGIVEN[shaman])

        target = 0
        rand = random.random() * max_totems
        for t in chances:
            target += chances[t]["wolf shaman"]
            if rand <= target:
                TOTEMS[shaman] = t
                break
        if shaman.prefers_simple():
            # Message about role was sent with wolfchat
            shaman.send(messages["totem_simple"].format(TOTEMS[shaman]))
        else:
            totem = TOTEMS[shaman]
            tmsg = messages["shaman_totem"].format(totem)
            tmsg += messages[totem + "_totem"]
            shaman.send(tmsg)

@event_listener("get_role_metadata")
def on_get_role_metadata(evt, var, kind):
    if kind == "role_categories":
        evt.data["wolf shaman"] = {"Wolf", "Wolfchat", "Wolfteam", "Killer", "Nocturnal"}

@event_listener("default_totems")
def set_wolf_totems(evt, chances):
    chances["protection"]   ["wolf shaman"] = 1
    chances["silence"]      ["wolf shaman"] = 1
    chances["impatience"]   ["wolf shaman"] = 1
    chances["pacifism"]     ["wolf shaman"] = 1
    chances["lycanthropy"]  ["wolf shaman"] = 1
    chances["luck"]         ["wolf shaman"] = 1
    chances["retribution"]  ["wolf shaman"] = 1
    chances["misdirection"] ["wolf shaman"] = 1
    chances["deceit"]       ["wolf shaman"] = 1

# vim: set sw=4 expandtab:
