import re
import random

import src.settings as var
from src.utilities import *
from src import users, channels, debuglog, errlog, plog
from src.decorators import command, event_listener
from src.containers import UserList, UserSet, UserDict, DefaultUserDict
from src.functions import get_players, get_all_players, get_main_role, get_target
from src.messages import messages
from src.status import try_misdirection, try_exchange
from src.events import Event
from src.cats import Neutral, Wolfteam

from src.roles.helper.seers import setup_variables

SEEN = setup_variables("augur")

@command("see", chan=False, pm=True, playing=True, silenced=True, phases=("night",), roles=("augur",))
def see(var, wrapper, message):
    """Use your paranormal powers to determine the role or alignment of a player."""
    if wrapper.source in SEEN:
        wrapper.send(messages["seer_fail"])
        return

    target = get_target(var, wrapper, re.split(" +", message)[0], not_self_message="no_see_self")
    if target is None:
        return

    target = try_misdirection(var, wrapper.source, target)
    if try_exchange(var, wrapper.source, target):
        return

    targrole = get_main_role(target)
    trole = targrole # keep a copy for logging

    evt = Event("investigate", {"role": targrole})
    evt.dispatch(var, wrapper.source, target)
    targrole = evt.data["role"]

    aura = "blue"
    if targrole in Wolfteam:
        aura = "red"
    elif targrole in Neutral:
        aura = "grey"
    wrapper.send(messages["augur_success"].format(target, aura))
    debuglog("{0} (augur) SEE: {1} ({2}) as {3} ({4} aura)".format(wrapper.source, target, trole, targrole, aura))

    SEEN.add(wrapper.source)

@event_listener("get_role_metadata")
def on_get_role_metadata(evt, var, kind):
    if kind == "role_categories":
        evt.data["augur"] = {"Village", "Nocturnal", "Spy", "Safe"}
    elif kind == "lycanthropy_role":
        evt.data["augur"] = {"role": "doomsayer", "prefix": "seer"}

# vim: set sw=4 expandtab:
