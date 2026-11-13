"""The notice MK8's Online Play shows without a network: how this build plays with others.

Upstream's notice (runtime/ui/online_rows.mm) says the game needs its online service even for SMM2's Course Practice.
For MK8 it names both ways: Nextendo Network (which lists MK8 Deluxe; its backend is kept by
runtime/mk8/services/net_config.c, untested here) and the launcher's room with the game's own LAN Play.
"""

OLD = '''        snprintf(t, sizeof t, "You're offline. The game needs its online service, even for Course Practice: open %s, "
                 "Online, and sign in to Nextendo Network.", menu);
'''
NEW = '''        snprintf(t, sizeof t, "You're offline. Nextendo Network (experimental, not tested with this build): open %s, "
                 "Online, sign in, choose Nextendo and restart the game. Or race friends over LAN: join a room in the "
                 "MK8-online launcher, then in Wireless Play hold L + R and press the left stick.", menu);  // MK8 (toolchain/online_notice.py)
'''


def prepare(runtime):
    source = runtime/'ui/online_rows.mm'
    text = source.read_text(encoding='utf-8')
    if text.count(OLD) != 1:
        raise ValueError('Online notice boundary changed upstream')
    source.write_text(text.replace(OLD, NEW), encoding='utf-8')
