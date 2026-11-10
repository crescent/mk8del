"""Controller applet: the focus messages MK8 4.0.1 waits for after nn::hid::ShowControllerSupport.

The upstream HLE answers ShowControllerSupport at once, without the library applet. On the Switch an applet in
front takes the application's focus and gives it back when it closes, each change announced by an AM
FocusStateChanged message (15) with GetCurrentFocusState reporting the new state. MK8 stays in its "applet open"
state until it sees that: + at character select (the controller screen) left the cursor ignoring every press.
The HLE now queues both changes; each queued message carries its focus state in the high bits, and
GetCurrentFocusState reports the state of the last message received.
"""

AM = [
    ('static bool preselected_popped;\n',
     'static bool preselected_popped;\n'
     'static uint8_t mk8_focus = 1;  // (toolchain/controller_applet.py) 1 in focus, 2 out of focus\n'),
    ('    preselected_popped = false;\n}',
     '    preselected_popped = false;\n    mk8_focus = 1;\n}'),
    ('static void common_state_getter(Service *s, IpcReq *req, IpcResp *resp) {',
     '''// A library applet opened and closed (toolchain/controller_applet.py): focus out, then back in.
void mk8_applet_focus_cycle(void) {
    pthread_mutex_lock(&g_klock);
    if (msg_event && msg_count + 2 <= (int)(sizeof msg_queue / sizeof msg_queue[0])) {
        msg_queue[msg_count++] = 2u << 16 | 15;  // FocusStateChanged: out of focus
        msg_queue[msg_count++] = 1u << 16 | 15;  // FocusStateChanged: in focus
        kernel_signal(msg_event);
    } else {
        LOG("[mk8:applet] focus messages not queued (event %d, %d queued)\\n", msg_event != NULL, msg_count);
    }
    pthread_mutex_unlock(&g_klock);
}

static void common_state_getter(Service *s, IpcReq *req, IpcResp *resp) {'''),
    ('            OUT(uint32_t, 0) = msg_queue[0];\n',
     '            OUT(uint32_t, 0) = msg_queue[0] & 0xFFFF;\n'
     '            if (msg_queue[0] >> 16) mk8_focus = (uint8_t)(msg_queue[0] >> 16);\n'),
    ('    case 9: OUT(uint8_t, 0) = 1; resp_size(resp, 1); break;   // GetCurrentFocusState: in focus',
     '    case 9: OUT(uint8_t, 0) = mk8_focus; resp_size(resp, 1); break;   // GetCurrentFocusState'),
]

INPUT = [
    ('HLE_FUNC("_ZN2nn3hid21ShowControllerSupportEPNS0_27ControllerSupportResultInfoERKNS0_20ControllerSupportArgE",',
     'extern void mk8_applet_focus_cycle(void);  // (toolchain/controller_applet.py)\n'
     'HLE_FUNC("_ZN2nn3hid21ShowControllerSupportEPNS0_27ControllerSupportResultInfoERKNS0_20ControllerSupportArgE",'),
    ('        n < min ? " - fewer than the game asks for" : "");\n    c->x[0] = 0;\n',
     '        n < min ? " - fewer than the game asks for" : "");\n    mk8_applet_focus_cycle();\n    c->x[0] = 0;\n'),
]


def apply(text, changes):
    for old, new in changes:
        if text.count(old) != 1:
            raise ValueError(f'Controller applet boundary changed: {old.strip()[:80]}')
        text = text.replace(old, new)
    return text


def prepare(runtime):
    for rel, changes in (('services/am.c', AM), ('hle/input.c', INPUT)):
        path = runtime/rel
        path.write_text(apply(path.read_text(encoding='utf-8'), changes), encoding='utf-8')
