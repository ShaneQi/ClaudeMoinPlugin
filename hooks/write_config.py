#!/usr/bin/env python3
"""SessionStart hook: copy the plugin's userConfig values to a private file.

Claude Code exports each plugin option to hooks as CLAUDE_PLUGIN_OPTION_<KEY>,
but not to commands Claude runs through the Bash tool, so moin.py cannot read
them directly. This hook writes them to ~/.config/moinmoin-plugin/config.json
(mode 0600), which moin.py reads by default.

Prints nothing: SessionStart hook output is added to Claude's context, and the
password must never end up there. Never fails the session.
"""
import json
import os
import sys
import tempfile

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "moinmoin-plugin")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def main():
    url = os.environ.get("CLAUDE_PLUGIN_OPTION_URL", "").strip()
    if not url:
        return  # plugin options not set: leave any existing file alone
    cfg = {
        "url": url,
        "username": os.environ.get("CLAUDE_PLUGIN_OPTION_USERNAME", "").strip(),
        "password": os.environ.get("CLAUDE_PLUGIN_OPTION_PASSWORD", ""),
    }
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, prefix=".config-")  # created 0600
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except BaseException:
        os.unlink(tmp)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"moinmoin: could not write config: {type(e).__name__}", file=sys.stderr)
    sys.exit(0)
