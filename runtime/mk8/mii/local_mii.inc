// Persistent identity and appearance overlay on a valid SDK-compatible CharInfo base.
static bool mk8_edited_mii_valid;
static uint8_t mk8_edited_mii[0x58];
static bool mk8_mii_updated;
static int mk8_hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}
static bool mk8_face_parameters(uint8_t face[9]) {
    static const uint8_t defaults[9] = {0,0,1,1,0,0,0,64,64};
    static const uint8_t limits[9] = {5,2,3,7,2,5,1,127,127};
    memcpy(face, defaults, 9);
    const char *encoded = getenv("MK8_MII_FACE");
    if (!encoded || !*encoded) return true; // Existing profiles use the original defaults.
    if (strlen(encoded) != 18) return false;
    for (int i = 0; i < 9; i++) {
        int a=mk8_hex_digit(encoded[2*i]), b=mk8_hex_digit(encoded[2*i+1]);
        if (a<0 || b<0 || a*16+b>limits[i]) return false;
        face[i]=(uint8_t)(a*16+b);
    }
    return true;
}
static void mk8_face_charinfo(uint8_t *out, const uint8_t face[9]) {
    // The procedural hairstyles are independent assets; these are valid SDK style selectors.
    static const uint8_t hair_types[] = {0,33,24,12};
    static const uint8_t eyes[] = {2,4,8};
    static const uint8_t eye_colors[] = {8,10,11,12,9,0};
    out[0x29]=face[7]; out[0x2a]=face[8];
    out[0x2d]=face[1]; out[0x2e]=face[0];
    out[0x31]=hair_types[face[2]]; out[0x32]=face[3];
    out[0x34]=eyes[face[4]]; out[0x35]=eye_colors[face[5]];
    out[0x3c]=face[3]; out[0x4f]=face[6] ? 1 : 0;
}
static bool mk8_profile_charinfo(uint8_t *out) {
    if (mk8_edited_mii_valid) {
        if (out) memcpy(out, mk8_edited_mii, sizeof mk8_edited_mii);
        return true;
    }
    const char *id = getenv("MK8_MII_ID"), *name = getenv("MK8_MII_NAME");
    const char *color = getenv("MK8_MII_COLOR"), *gender = getenv("MK8_MII_GENDER");
    if (!id || strlen(id) != 32 || !name || !*name || !color || !gender) return false;
    uint8_t bytes[16];
    for (int i = 0; i < 16; i++) {
        int a = mk8_hex_digit(id[2*i]), b = mk8_hex_digit(id[2*i+1]);
        if (a < 0 || b < 0) return false;
        bytes[i] = a*16+b;
    }
    if ((bytes[8] & 0xc0) != 0x80 || strlen(gender) != 1 || *gender < '0' || *gender > '1') return false;
    int favorite;
    if (strlen(color) == 1 && *color >= '0' && *color <= '9') favorite = *color-'0';
    else if (!strcmp(color,"10")) favorite = 10;
    else if (!strcmp(color,"11")) favorite = 11;
    else return false;
    uint8_t face[9];
    if (!mk8_face_parameters(face)) return false;
    if (out) {
        mii_default_charinfo(out, name);
        memcpy(out, bytes, sizeof bytes);
        out[0x27] = favorite;
        out[0x28] = *gender-'0';
        mk8_face_charinfo(out, face);
    }
    return true;
}

static unsigned mk8_mii_count(uint32_t sources, bool local) {
    return ((sources & 1) && local ? 1 : 0) + (sources & 2 ? 6 : 0);
}

static unsigned mk8_mii_get(uint8_t *out, size_t bytes, size_t stride, uint32_t sources, bool local);
// UpdateLatest/GetIndex against the database's one local entry, matched by create ID (first 16 bytes):
// 0 = found and newer (copied to latest), else the nn::mii result description 3 NotUpdated / 4 NotFound.
static unsigned mk8_mii_latest(const uint8_t *info, uint32_t sources, bool local, uint8_t latest[0x58]) {
    if (!mk8_mii_get(latest, 0x58, 0x58, sources & 1, local) || memcmp(latest, info, 16)) return 4;
    return memcmp(latest, info, 0x58) ? 0 : 3;
}

static unsigned mk8_mii_get(uint8_t *out, size_t bytes, size_t stride, uint32_t sources, bool local) {
    if (!out || (stride != 0x58 && stride != 0x5c)) return 0;
    unsigned count = 0;
    if ((sources & 1) && local && bytes >= stride) {
        memset(out, 0, stride);
        if (!mk8_profile_charinfo(out)) mii_default_charinfo(out, "Player");
        count++;
    }
    // Six data-only presets; no mesh or portrait is supplied by these CharInfo records.
    for (unsigned i = 0; (sources & 2) && i < 6 && (count + 1) * stride <= bytes; i++) {
        uint8_t *p = out + count * stride;
        memset(p, 0, stride);
        mii_default_charinfo(p, "Default");
        p[15] += i; p[0x27] = i;
        if (stride == 0x5c) p[0x58] = 1; // Source::Default, not the user's database.
        count++;
    }
    return count;
}
