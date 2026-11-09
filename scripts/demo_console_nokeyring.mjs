// Preloaded by scripts/demo_console.py (node --import). Replaces the native OS-keyring binding with one that
// holds nothing, so the demo console cannot find a broker credential however the machine is set up.
import { register } from "node:module";

register("./demo_console_nokeyring_hooks.mjs", import.meta.url);
