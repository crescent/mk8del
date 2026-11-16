#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdlib.h>

NvnTexture *mk8_texture_retain(NvnTexture *t) {
    if (t && !__atomic_fetch_add(&t->mk8_refs, 1, __ATOMIC_RELAXED))
        FATAL("Retaining a retired texture\n");
    return t;
}

// The texture backends (nvn/vulkan/textures.c: images, views, the alias table, the live list, the deferred-destruction
// list) have no lock: they belong to the thread that translates commands. With async translation
// (toolchain/async_submit.py) that is its own thread, while the last reference of a texture drops on the game's threads
// (nvnTextureFinalize, command lists released when recording). Destroying the backend there raced with the
// translation: a realloc of the deferred list under the translator (STATUS_HEAP_CORRUPTION in a menu) and a view created
// over an image already destroyed (an access violation inside the driver's vkCreateImageView at the start of a race).
// Such textures now wait in this list; the translator destroys them before its next item (toolchain/texture_release_thread.py).
// Before the translator starts (and with MK8_ASYNC_SUBMIT=0) there is only one thread translating: destroyed at once.
static pthread_mutex_t mk8_retired_lock = PTHREAD_MUTEX_INITIALIZER;
static NvnTexture **mk8_retired;
static size_t mk8_nretired, mk8_cap_retired;
static _Atomic bool mk8_translator_running;
static __thread bool mk8_on_translator;

static void mk8_texture_destroy_now(NvnTexture *t) {
    backend_texture_destroyed(t);
    free(t);
}

void mk8_texture_translator_thread(void) {
    mk8_on_translator = true;
    atomic_store(&mk8_translator_running, true);
}

static void mk8_textures_detach_pending(void);
void mk8_textures_drain(void) {
    mk8_textures_detach_pending();
    pthread_mutex_lock(&mk8_retired_lock);
    NvnTexture **list = mk8_retired;
    size_t n = mk8_nretired;
    mk8_retired = NULL;
    mk8_nretired = mk8_cap_retired = 0;
    pthread_mutex_unlock(&mk8_retired_lock);
    for (size_t i = 0; i < n; i++) mk8_texture_destroy_now(list[i]);
    free(list);
}

void mk8_texture_release(NvnTexture *t) {
    if (!t) return;
    unsigned previous = __atomic_fetch_sub(&t->mk8_refs, 1, __ATOMIC_ACQ_REL);
    if (!previous) FATAL("Texture reference underflow\n");
    if (previous != 1) return;
    if (mk8_on_translator || !atomic_load(&mk8_translator_running)) {
        mk8_texture_destroy_now(t);
        return;
    }
    pthread_mutex_lock(&mk8_retired_lock);
    if (mk8_nretired == mk8_cap_retired) {
        size_t cap = mk8_cap_retired ? mk8_cap_retired * 2 : 1024;
        NvnTexture **grown = realloc(mk8_retired, cap * sizeof *grown);
        if (!grown) FATAL("Retired texture list allocation failed\n");
        mk8_retired = grown;
        mk8_cap_retired = cap;
    }
    mk8_retired[mk8_nretired++] = t;
    pthread_mutex_unlock(&mk8_retired_lock);
}

// Guest texture objects and the texture each one last held. On the Switch an NVN texture object is plain data: MK8
// initializes texture objects on its stack again every frame without nvnTextureFinalize (about 20 per frame, 1920x1080
// render targets among them), and sometimes finalizes a heap copy of one later. The texture objects therefore stay
// (a copy may still use them), but the earlier texture lets go of its GPU image; a later use takes the image back from
// the alias table (nvn/vulkan/textures.c, ~2 s). Before this, with MK8's dynamic resolution (1862x1046, 1856x1044...),
// every size ever used kept its images: online, after a few races, the graphics card ran out of memory.
// The map is keyed by the guest object address; guest bytes are never trusted.
typedef struct Mk8Owner { uint64_t object; NvnTexture *t; struct Mk8Owner *next; } Mk8Owner;
static Mk8Owner *mk8_owners[4096];
static pthread_mutex_t mk8_owner_lock = PTHREAD_MUTEX_INITIALIZER;
static Mk8Owner **mk8_owner_slot(uint64_t object) {
    Mk8Owner **pp = &mk8_owners[(object >> 4) & 4095];
    while (*pp && (*pp)->object != object) pp = &(*pp)->next;
    return pp;
}

// The texture this object held before and was never finalized, or NULL.
NvnTexture *mk8_texture_adopt(uint64_t object, NvnTexture *t) {
    t->mk8_owner = object;
    pthread_mutex_lock(&mk8_owner_lock);
    Mk8Owner **pp = mk8_owner_slot(object);
    NvnTexture *old = *pp ? (*pp)->t : NULL;
    if (*pp) (*pp)->t = t;
    else {
        Mk8Owner *e = malloc(sizeof *e);
        if (!e) FATAL("Texture owner allocation failed\n");
        *e = (Mk8Owner){object, t, mk8_owners[(object >> 4) & 4095]};
        mk8_owners[(object >> 4) & 4095] = e;
    }
    pthread_mutex_unlock(&mk8_owner_lock);
    return old;
}

// (nvnTextureFinalize, through the object or a copy of it)
void mk8_texture_disown(NvnTexture *t) {
    pthread_mutex_lock(&mk8_owner_lock);
    Mk8Owner **pp = mk8_owner_slot(t->mk8_owner);
    if (*pp && (*pp)->t == t) { Mk8Owner *e = *pp; *pp = e->next; free(e); }
    pthread_mutex_unlock(&mk8_owner_lock);
}

// The image goes on the translating thread, where backends live (drained with the retired textures).
void mk8_texture_detach(NvnTexture *t);
static NvnTexture **mk8_detached;
static size_t mk8_ndetached, mk8_cap_detached;
void mk8_texture_abandoned(NvnTexture *t) {
    mk8_texture_retain(t);
    if (mk8_on_translator || !atomic_load(&mk8_translator_running)) {
        mk8_texture_detach(t);
        mk8_texture_release(t);
        return;
    }
    pthread_mutex_lock(&mk8_retired_lock);
    if (mk8_ndetached == mk8_cap_detached) {
        size_t cap = mk8_cap_detached ? mk8_cap_detached * 2 : 1024;
        NvnTexture **grown = realloc(mk8_detached, cap * sizeof *grown);
        if (!grown) FATAL("Detached texture list allocation failed\n");
        mk8_detached = grown;
        mk8_cap_detached = cap;
    }
    mk8_detached[mk8_ndetached++] = t;
    pthread_mutex_unlock(&mk8_retired_lock);
}

static void mk8_textures_detach_pending(void) {
    pthread_mutex_lock(&mk8_retired_lock);
    NvnTexture **list = mk8_detached;
    size_t n = mk8_ndetached;
    mk8_detached = NULL;
    mk8_ndetached = mk8_cap_detached = 0;
    pthread_mutex_unlock(&mk8_retired_lock);
    for (size_t i = 0; i < n; i++) {
        mk8_texture_detach(list[i]);
        mk8_texture_release(list[i]);
    }
    free(list);
}
