#!/usr/bin/env python3
"""SessionStart hook: hand the plugin's userConfig values to moin.py.

Claude Code exports each plugin option to hooks as CLAUDE_PLUGIN_OPTION_<KEY>,
but not to commands Claude runs through the Bash tool. So this hook:

1. writes the values to config.json (mode 0600) in the plugin's data directory
   ($CLAUDE_PLUGIN_DATA, or ~/.config/moinmoin-plugin when that is unset);
2. appends `export MOIN_CONFIG=<that path>` to $CLAUDE_ENV_FILE, so later Bash
   commands find it wherever this client keeps plugin data (only the path goes
   there, never the password);
3. writes status.json next to it: when the hook ran and which options were set,
   without their values, for moinmoin-check to report.

Prints nothing: SessionStart hook output is added to Claude's context, and the
password must never end up there. Never fails the session.
"""
import datetime as dt
import json
import os
import shlex
import sys
import tempfile


def config_dir():
    d = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    return d or os.path.join(os.path.expanduser("~"), ".config", "moinmoin-plugin")


def write_private(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")  # created 0600
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def main():
    opt = lambda k: os.environ.get(f"CLAUDE_PLUGIN_OPTION_{k}", "")
    url, user, pw = opt("URL").strip(), opt("USERNAME").strip(), opt("PASSWORD")
    d = config_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, "config.json")
    if url:  # options not set: leave any existing file alone
        write_private(path, {"url": url, "username": user, "password": pw})

    env_file = os.environ.get("CLAUDE_ENV_FILE", "").strip()
    exported = False
    if env_file and os.path.isfile(path):
        with open(env_file, "a") as f:
            f.write(f"export MOIN_CONFIG={shlex.quote(path)}\n")
        exported = True

    write_private(os.path.join(d, "status.json"), {
        "hook_ran_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "url_set": bool(url), "username_set": bool(user), "password_set": bool(pw),
        "config_written": bool(url), "config_path": path, "exported_MOIN_CONFIG": exported,
    })


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"moinmoin: hook failed: {type(e).__name__}", file=sys.stderr)
    sys.exit(0)
