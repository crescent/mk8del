"""The Online Play notice and the split-screen diagnostics apply to the pinned upstream and refuse other versions."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'toolchain'))
from host import prepare_host_sources
from native import DEFAULT_COMPILER, compiler_env
import nextendo_services
import online_notice
import split_screen


class ModePatchTests(unittest.TestCase):
    def test_prepared_runtime_has_the_mk8_notice_and_pad_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', Path(folder))
            notice = (runtime/'ui/online_rows.mm').read_text(encoding='utf-8')
            self.assertIn('join a room in the "\n                 "MK8-online launcher', notice)
            self.assertIn('choose Nextendo and restart the game', notice)
            self.assertNotIn('sign in to Nextendo Network.", menu);', notice)
            # (the signed-in branches of upstream stay as they are)
            self.assertIn("You're signed in to Nextendo", notice)
            source = (runtime/'hle/input.c').read_text(encoding='utf-8')
            self.assertIn('MK8_TRACE_PADS', source)
            self.assertEqual(source.count('mk8_pad_kind = '), 6)
            self.assertIn('MK8_TRACE_IMPORTS', (runtime/'loader.c').read_text(encoding='utf-8'))
            mii = (runtime/'services/mii.c').read_text(encoding='utf-8')
            self.assertIn('case 5: case 21:', mii)
            self.assertLess(mii.index('case 5: case 21:'), mii.index('case 22: break;'))
            stub = (runtime/'ipc.c').read_text(encoding='utf-8')
            self.assertLess(stub.index('[mk8:prepo] cmd %u'), stub.index('resp_size(resp, 0x40);'))
            textures = (runtime/'nvn/vulkan/textures.c').read_text(encoding='utf-8')
            self.assertLess(textures.index('mk8_vram_report();'), textures.index('[vk] image creation failed'))
            ssl = (runtime/'services/ssl.c').read_text(encoding='utf-8')
            self.assertIn('!ssl_pin_check(peer_crt, services_pin)', ssl)
            self.assertIn(', '.join(f'0x{b:02x}' for b in bytes.fromhex(nextendo_services.SERVICES_PIN)), ssl)

    @unittest.skipUnless(DEFAULT_COMPILER.is_file(), 'Native compiler required')
    def test_udp_port_binds_the_games_port_to_the_fixed_host_port(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            runtime = prepare_host_sources(ROOT/'upstream/smm2recomp/recomp', out/'host')
            source = out/'udp.c'
            source.write_text(r'''#include "services/bsd_host.c"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
volatile int g_rt_unwind;
static const char *cfg = "";
bool game_net_config(const char *key, char *o, size_t cap) {
    if (strcmp(key, "udp_port") || !*cfg) return false;
    snprintf(o, cap, "%s", cfg);
    return true;
}
void rt_log(const char *fmt, ...) { va_list a; va_start(a, fmt); vfprintf(stderr, fmt, a); va_end(a); }
uint64_t plat_now_ns(void) { return (uint64_t)GetTickCount64() * 1000000; }
NetBackend net_backend(void) { return NET_BACKEND_NEXTENDO; }
bool net_ip_is_local(uint32_t ip) { (void)ip; return true; }
uint32_t net_test_server_ip(void) { return 0; }
bool net_log_on(void) { return false; }
void ip_to_str(uint32_t ip, char o[16]) { snprintf(o, 16, "%u", ip); }
void kernel_host_wait_begin(void) {}
void kernel_host_wait_end(void) {}
bool net_dest_allowed(uint32_t ip) { (void)ip; return false; }
bool net_dest_allowed_udp(uint32_t ip) { (void)ip; return false; }
uint16_t net_dest_port(uint32_t ip, uint16_t port) { (void)ip; return port; }
void net_log_redact(const char *in, char *o, size_t cap) { snprintf(o, cap, "%s", in); }
bool net_ocw_host(char *o, size_t cap) { (void)o; (void)cap; return false; }
bool game_http_answer(uint32_t ip, uint16_t port, const void *r, size_t n, uint8_t **o, size_t *on) {
    (void)ip; (void)port; (void)r; (void)n; (void)o; (void)on; return false;
}
static int udp(void) {
    int32_t args[3] = {2, 2, 17};
    IpcReq req = {.data = (const uint8_t *)args, .data_size = sizeof args};
    IpcResp resp = {0};
    do_socket(&req, &resp);
    int fd = *(int32_t *)resp.data;
    assert(fd >= 0);
    return fd;
}
static int bind_to(int fd, uint16_t port) {  // (the game's view: 0.0.0.0:port)
    uint8_t a[16] = {16, 2, (uint8_t)(port >> 8), (uint8_t)port};
    IpcReq req = {.na = 1};
    req.a[0] = (IpcBuf){(uintptr_t)a, sizeof a};
    IpcResp resp = {0};
    do_bind(table[fd], fd, &req, &resp);
    return *(int32_t *)resp.data;
}
static void close_fd(int fd) {  // (Close, command 26: a bound UDP socket is parked)
    BsdSock *s = table[fd];
    table[fd] = NULL, s->closed = true;  // (its reference goes with bsd_sock_unref)
    bsd_sock_unref(s);
}
static uint16_t host_port(int fd) { return host_port_of(table[fd]->h); }
int main(void) {
    hsock_startup();
    // no udp_port: the game's own port, as before
    int a = udp();
    assert(bind_to(a, 47001) == 0 && host_port(a) == 47001 && table[a]->guest_port == 47001);
    close_fd(a);
    cfg = "47123";
    // the fixed host port; the game keeps seeing its own
    int b = udp();
    assert(bind_to(b, 47002) == 0 && host_port(b) == 47123 && table[b]->guest_port == 47002);
    hsock_t first = table[b]->h;
    close_fd(b);  // (parked: its NAT mapping kept)
    // the next session's port takes the parked host socket over
    int c = udp();
    assert(bind_to(c, 47003) == 0 && table[c]->h == first && host_port(c) == 47123 && table[c]->guest_port == 47003);
    // a second socket at the same time: another host port, not a failure
    int d = udp();
    assert(bind_to(d, 47004) == 0 && host_port(d) != 47123 && host_port(d) && table[d]->guest_port == 47004);
    // port 0 (any port) is left alone
    int e = udp();
    assert(bind_to(e, 0) == 0 && host_port(e) != 47123);
    close_fd(c); close_fd(d); close_fd(e);
    // a datagram to a closed port: Windows' ICMP "port unreachable" must not surface as a receive error
    SOCKET probe = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in to = {.sin_family = AF_INET, .sin_addr.s_addr = htonl(0x7f000001)};
    int tl = sizeof to;
    assert(!bind(probe, (struct sockaddr *)&to, sizeof to) && !getsockname(probe, (struct sockaddr *)&to, &tl));
    closesocket(probe);  // (its port is now closed)
    int f = udp();
    for (int i = 0; i < 3; i++) assert(sendto(table[f]->h, "x", 1, 0, (struct sockaddr *)&to, sizeof to) == 1);
    Sleep(200);
    char got[8];
    assert(recvfrom(table[f]->h, got, sizeof got, 0, NULL, NULL) < 0 && WSAGetLastError() == WSAEWOULDBLOCK);
    close_fd(f);
    // an invalid value is ignored
    cfg = "80";
    assert(mk8_udp_port() == 0);
    return 0;
}
''', encoding='utf-8')
            exe = out/'udp.exe'
            build = subprocess.run([str(DEFAULT_COMPILER), 'cc', '-target', 'x86_64-windows-gnu', '-UNDEBUG', '-O1',
                                    '-I'+str(runtime), str(source), '-o', str(exe), '-lws2_32'],
                                   capture_output=True, text=True, env=compiler_env())
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('[bsd] udp_port 47123: the game', run.stderr)

    def test_boundaries_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime/'ui').mkdir()
            (runtime/'hle').mkdir()
            (runtime/'ui/online_rows.mm').write_text('int x;\n')
            (runtime/'hle/input.c').write_text('int y;\n')
            with self.assertRaises(ValueError):
                online_notice.prepare(runtime)
            with self.assertRaises(ValueError):
                split_screen.prepare(runtime)
            (runtime/'services').mkdir()
            (runtime/'services/ssl.c').write_text('int z;\n')
            with self.assertRaises(ValueError):
                nextendo_services.prepare(runtime)


if __name__ == '__main__':
    unittest.main()
