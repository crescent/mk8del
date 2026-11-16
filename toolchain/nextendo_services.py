"""Nextendo's console-service hosts pass the pinned-certificate check (runtime/services/ssl.c).

With the Nextendo backend upstream accepts one key on every connection: the game server's. Nextendo serves its
console-service hosts (api-lp1.znc.srv.nintendo.net, baas, eshop...: one certificate, CN=accounts.nintendo.com,
issued by "Nextendo Network CA", 2026-10-31..2036-10-31) with another key [obs, 2026-11-12], so MK8's calls to
api-lp1.znc were refused after the game server logged in. This also accepts that certificate's SPKI SHA-256.
The game server keeps its own pin (runtime/mk8/services/net_config.c); no other trust mode changes.
"""

SERVICES_PIN = 'eb22631902f276218368e59dfac73f656bf8391c356e6c37f079049db40d596f'

OLD = '''    uint8_t pin[32];
    if (net_tls_trust() == NET_TRUST_PINNED && (!net_tls_pin(pin) || !ssl_pin_check(mbedtls_ssl_get_peer_cert(&c->ssl), pin))) {
'''


def _new():
    data = bytes.fromhex(SERVICES_PIN)
    array = ', '.join(f'0x{b:02x}' for b in data)
    return f'''    uint8_t pin[32];
    static const uint8_t services_pin[32] = {{{array}}};  // MK8: Nextendo's service hosts (toolchain/nextendo_services.py)
    const mbedtls_x509_crt *peer_crt = mbedtls_ssl_get_peer_cert(&c->ssl);
    if (net_tls_trust() == NET_TRUST_PINNED &&
        (!net_tls_pin(pin) || (!ssl_pin_check(peer_crt, pin) && !ssl_pin_check(peer_crt, services_pin)))) {{
'''


# udp_port (mk8net.ini): the game's peer-to-peer UDP socket (PIA) on one host port the player can forward on the
# router. PIA picks a new port each session (55688, then 55933 [obs, 2026-11-15]), so a forward was impossible and a
# strict NAT ended rooms with error 2618-0519. The game keeps seeing its own port, as with a port taken on the host.
BIND = '''    uint16_t hport = port;
    pthread_mutex_lock(&lock);
    for (int i = 0; i < nport_map; i++)
        if (port && port_map[i].guest == port) hport = port_map[i].host;
    pthread_mutex_unlock(&lock);
'''

BIND_FIXED = BIND + '''    uint16_t fixed = s->type == 2 && port ? mk8_udp_port() : 0;
    if (fixed) {  // (the fixed port parked by an earlier guest port: taken over, its NAT mapping kept)
        pthread_mutex_lock(&lock);
        hsock_t h = mk8_unpark_host(fixed);
        pthread_mutex_unlock(&lock);
        if (h != HSOCK_BAD) {
            hsock_close(s->h);
            s->h = h, s->bound = true, s->bound_ip = ip, s->guest_port = port;
            NLOG("[bsd] bind %d: UDP port %u on the fixed host port %u (udp_port), parked one reused\\n", fd, port, fixed);
            result(resp, 0, 0);
            return;
        }
        hport = fixed;
    }
'''

DO_BIND = 'static void do_bind(BsdSock *s, int fd, IpcReq *req, IpcResp *resp) {\n'

HELPERS = '''// MK8 (toolchain/nextendo_services.py): udp_port from mk8net.ini, 0 none (1024-65535)
bool game_net_config(const char *key, char *out, size_t cap);
static uint16_t mk8_udp_port(void) {
    char v[16] = "";
    if (!game_net_config("udp_port", v, sizeof v) || !*v) return 0;
    char *end;
    long p = strtol(v, &end, 10);
    static bool logged;
    bool ok = !*end && p >= 1024 && p <= 65535;
    if (!logged) LOG(ok ? "[bsd] udp_port %s: the game's UDP port is bound to that host port\\n"
                        : "[bsd] udp_port \\"%s\\" ignored (1024-65535)\\n", v);
    logged = true;
    return ok ? (uint16_t)p : 0;
}

// under lock: the parked host socket bound to host port hport (taken out), HSOCK_BAD none
static hsock_t mk8_unpark_host(uint16_t hport) {
    uint64_t now = plat_now_ns();
    for (int i = 0; i < nparked; i++)
        if (parked[i].until > now && host_port_of(parked[i].h) == hport) {
            hsock_t h = parked[i].h;
            parked[i] = parked[--nparked];
            return h;
        }
    return HSOCK_BAD;
}

'''

# Windows reports an ICMP "port unreachable" for a sent datagram as WSAECONNRESET on the socket's next recvfrom; a
# Switch's unconnected UDP socket never does. In an online race every player's traffic stopped within 2 s while the
# game kept sending, and it ended the race with error 2618-0006, 6 of 7 opponents "disconnected" [obs, 2026-11-15].
# The game's peer socket gets the console's behaviour; receive errors are logged (net_log) to confirm the cause.
NEW_SOCKET = '''    hsock_nonblock(h);
    BsdSock *s = calloc(1, sizeof *s);
'''
NEW_SOCKET_FIXED = '''    hsock_nonblock(h);
#ifdef _WIN32
    if (type == 2) {  // MK8 (toolchain/nextendo_services.py): no WSAECONNRESET from ICMP on UDP, as on the Switch
        BOOL off = FALSE;
        DWORD unused = 0;
        if (WSAIoctl(h, _WSAIOW(IOC_VENDOR, 12) /* SIO_UDP_CONNRESET */, &off, sizeof off, NULL, 0, &unused, NULL, NULL))
            LOG("[bsd] UDP socket: ICMP resets stay on (WSAIoctl error %d)\\n", WSAGetLastError());
    }
#endif
    BsdSock *s = calloc(1, sizeof *s);
'''
RECV_ERROR = '''            result_len(resp, -1, hsock_bsd_errno(e), 0);
'''
RECV_ERROR_LOGGED = '''            NLOG("[bsd] recv %d: error %d (host %d)\\n", fd, hsock_bsd_errno(e), e);  // MK8
            result_len(resp, -1, hsock_bsd_errno(e), 0);
'''


def _patch(path, pairs, what):
    text = path.read_text(encoding='utf-8')
    for old, new in pairs:
        if text.count(old) != 1:
            raise ValueError(f'{what} boundary changed upstream')
        text = text.replace(old, new)
    path.write_text(text, encoding='utf-8')


def prepare(runtime):
    _patch(runtime/'services/ssl.c', [(OLD, _new())], 'Nextendo pin check')
    _patch(runtime/'services/bsd_host.c', [(DO_BIND, HELPERS + DO_BIND), (BIND, BIND_FIXED),
                                           (NEW_SOCKET, NEW_SOCKET_FIXED), (RECV_ERROR, RECV_ERROR_LOGGED)], 'UDP socket')
