// MK8's store for the runtime network settings (upstream runtime/net/netcfg.c reads them through the weak
// game_net_config / game_net_config_set of runtime/game_hooks.c). Without it the menu's Online backend wrote nowhere:
// Nextendo, chosen and followed by "Restart the game now", came back as "ours". SMM2 keeps these keys in its mods ini;
// MK8 keeps them in mk8net.ini in the data folder (the working directory), not in mk8d.ini, which the runtime rewrites
// from memory with only its own keys. Environment variables (RECOMP_NET_*) still win, as netcfg.c reads them first.
#include <windows.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#define NET_INI "mk8net.ini"

static bool known(const char *key) {
    static const char *const keys[] = {"backend", "nex_test_server", "net_hosts", "tls_trust", "tls_ca",
                                       "id_token_file", "nex_token_file", "net_log", "net_log_data",
                                       "nextendo_server", "nextendo_nat", "nextendo_tls_pin",
                                       "udp_port"};  // (toolchain/nextendo_services.py)
    for (size_t i = 0; i < sizeof keys / sizeof *keys; i++)
        if (!strcmp(keys[i], key)) return true;
    return false;
}

// "key=value" (spaces around '=' allowed); the last line for a key wins
static bool value_of(const char *line, const char *key, char *out, size_t cap) {
    size_t kl = strlen(key);
    if (strncmp(line, key, kl)) return false;
    const char *p = line + kl;
    while (*p == ' ') p++;
    if (*p++ != '=') return false;
    while (*p == ' ') p++;
    size_t n = strcspn(p, "\r\n");
    while (n && p[n - 1] == ' ') n--;
    snprintf(out, cap, "%.*s", (int)n, p);
    return true;
}

// Nextendo's game server picks its self-signed certificate by SNI [obs, 2026-11-12]: SMM2's host gets the key upstream
// pins, MK8's g2b309e01-lp1.s.n.srv.nintendo.net gets another (CN=nintendo-private-server, 2026-07-13..2036-07-10),
// so the upstream pin refused every MK8 connection and the game showed error 2306-0502. This is the SHA-256 of MK8's
// SubjectPublicKeyInfo; nextendo_tls_pin in mk8net.ini (or RECOMP_NEXTENDO_TLS_PIN) still overrides it.
#define MK8_NEXTENDO_PIN "770551f13152f3d55370de6c039d3859cff0d39882f27c6c9fc6cfdb327d74b8"

bool game_net_config(const char *key, char *out, size_t cap) {
    FILE *f = known(key) ? fopen(NET_INI, "r") : NULL;
    char line[1024];
    bool found = false;
    while (f && fgets(line, sizeof line, f))
        if (line[0] != '#') found |= value_of(line, key, out, cap);
    if (f) fclose(f);
    if (!found && !strcmp(key, "nextendo_tls_pin")) snprintf(out, cap, "%s", MK8_NEXTENDO_PIN), found = true;
    return found;
}

// Rewrites the file with the key's line replaced (or added); every other line stays.
void game_net_config_set(const char *key, const char *val) {
    if (!known(key) || strpbrk(val, "\r\n")) return;
    static char text[16384];
    size_t len = 0;
    char line[1024], old[1024];
    FILE *f = fopen(NET_INI, "r");
    while (f && fgets(line, sizeof line, f)) {
        if (line[0] != '#' && value_of(line, key, old, sizeof old)) continue;  // (the key's lines: written last)
        int n = snprintf(text + len, sizeof text - len, "%s", line);
        if (n < 0 || (size_t)n >= sizeof text - len) { fclose(f); return; }  // (too long: left as it was)
        len += n;
    }
    if (f) fclose(f);
    if (len && text[len - 1] != '\n') text[len++] = '\n';  // (len < sizeof text: the snprintf above left room)
    int n = snprintf(text + len, sizeof text - len, "%s=%s\n", key, val);
    if (n < 0 || (size_t)n >= sizeof text - len) return;
    len += n;
    FILE *w = fopen(NET_INI ".new", "wb");
    if (!w) return;
    bool ok = fwrite(text, 1, len, w) == len;
    if (fclose(w) == 0 && ok) MoveFileExA(NET_INI ".new", NET_INI, MOVEFILE_REPLACE_EXISTING);
}
