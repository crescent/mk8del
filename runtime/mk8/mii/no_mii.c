#include "hle.h"
#include <string.h>
#include <stdatomic.h>

// shortcut: bypass verified Mii preparation and model policy paths. Other Mii
// consumers still need qualification before this can be called a playable mode.
int mk8_no_mii_renderer(void) {
    const char *mode = getenv("MK8_EXPERIMENTAL_NO_MII");
    if (!mode || strcmp(mode, "1")) return 0;
    static atomic_flag announced = ATOMIC_FLAG_INIT;
    if (!atomic_flag_test_and_set(&announced))
        LOG("[mk8:no-mii] Mii manager preparation/update disabled; gameplay unqualified\n");
    return 1;
}

// A model's part list with an empty entry (toolchain/experimental.py, f_700069127c): suspected absent Mii model.
// No-Mii mode only: the part is skipped and logged; otherwise the original null access stays.
int mk8_no_mii_null_part(uint64_t owner) {
    if (!mk8_no_mii_renderer()) return 0;
    static atomic_uint skipped;
    unsigned n = atomic_fetch_add(&skipped, 1) + 1;
    if (n <= 8 || n % 1000 == 0)
        LOG("[mk8:no-mii] model %llx: empty entry in its part list skipped (%u so far)\n", (unsigned long long)owner, n);
    return 1;
}
