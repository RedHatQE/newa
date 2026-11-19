#!/usr/bin/env python3
"""Bitwarden credential access for the scripts in this directory.

Version: 1.0.7

No credential is read from environment variables. Scripts ask for a named
secret, which is fetched from Bitwarden through the official `bw` CLI.

Vault layout assumed by SECRETS below — edit the names/fields to match yours:

    Jira user1     custom field "email" = your email, "PAT" = API token
    Jira user2     custom field "PAT" = API token (user2 account)
    GitLab            custom field "PAT" = personal access token
    Testing Farm      custom field "PAT" = Testing Farm API token
    ReportPortal      custom field "PAT" = ReportPortal API token

Session handling, in order:
    1. $BW_SESSION, if set and still valid
    2. a session cached in $XDG_RUNTIME_DIR/bw-session-<uid>, if younger than
       $BW_CACHE_TTL seconds (default 600)
    3. one master-password prompt, whose session is then cached

If `bw status` reports unauthenticated, the user is first prompted for the
account email and master password; `bw login` itself prompts for a 2FA code
when the account requires one. The resulting session is cached as usual.

The session key is held in memory and passed only to the `bw` child process;
it is never written into this process's os.environ.

Switches:
    BW_CACHE_TTL=600   seconds a cached session may be reused
    BW_NO_CACHE=1      never read or write the session cache
    BW_LOCK_AFTER=1    run `bw lock` and drop the cache when the process exits

Library use:
    from bw_credentials import BwError, get_secret
    token = get_secret("JIRA_API_TOKEN")

Command line (useful for checking the mapping):
    ./bw_credentials.py --version
    ./bw_credentials.py --list
    ./bw_credentials.py --fields "GitLab"
    ./bw_credentials.py JIRA_API_TOKEN

Field names: username/password/totp/notes/uri are built-in item fields; any
other value in SECRETS is treated as the name of a custom field, e.g.
    "GITLAB_TOKEN": ("GitLab", "pat"),
"""

import atexit
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

__version__ = "1.0.7"

# env-var name -> (Bitwarden item name, item field)
SECRETS = {
    "JIRA_EMAIL": ("Jira user1", "email"),
    "JIRA_API_TOKEN": ("Jira user1", "PAT"),
    "JIRA_API_TOKEN_SE": ("Jira se-security", "PAT"),
    "GITLAB_TOKEN": ("GitLab", "PAT"),
    "TESTING_FARM_API_TOKEN": ("Testing Farm user1", "PAT"),
    "RP_TOKEN": ("ReportPortal", "PAT"),
}

BUILTIN_FIELDS = ("password", "username", "totp", "notes", "uri")
DEFAULT_CACHE_TTL = 600
_session = None
_lock_registered = False
_values = {}
_item_ids = {}
_synced = False
_sync_error = None


class BwError(RuntimeError):
    """Raised when Bitwarden cannot provide the requested credential."""


def _run_bw(*args, extra_env=None, check=True):
    # The session key is passed only to the bw child process, never left in
    # this process's environment.
    env = {k: v for k, v in os.environ.items() if k != "BW_SESSION"}
    if _session:
        env["BW_SESSION"] = _session
    env.update(extra_env or {})
    proc = subprocess.run(["bw", *args], capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise BwError(proc.stderr.strip() or f"bw {' '.join(args)} failed")
    return proc


def _session_is_valid(session):
    """A harmless vault command succeeds only if the session works."""
    proc = _run_bw(
        "list",
        "items",
        "--search",
        "bw-session-probe",
        extra_env={"BW_SESSION": session},
        check=False,
    )
    return proc.returncode == 0


def _cache_ttl():
    try:
        return int(os.environ.get("BW_CACHE_TTL", DEFAULT_CACHE_TTL))
    except ValueError:
        return DEFAULT_CACHE_TTL


def _read_cache(cache_path, ttl):
    try:
        with open(cache_path, encoding="utf-8") as fh:
            session = fh.readline().rstrip("\n")
            stamp = fh.readline().strip()
    except OSError:
        return ""
    if not session or not stamp.isdigit():
        return ""
    if time.time() - int(stamp) > ttl:
        return ""
    return session


def _default_cache_path():
    cache_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(cache_dir, f"bw-session-{os.getuid()}")


def _maybe_lock_on_exit(cache_path):
    """Register a one-time atexit hook when BW_LOCK_AFTER=1."""
    global _lock_registered
    if os.environ.get("BW_LOCK_AFTER") != "1" or _lock_registered:
        return
    _lock_registered = True

    def cleanup():
        try:
            _run_bw("lock", check=False)
        except OSError:
            pass
        if cache_path:
            try:
                os.unlink(cache_path)
            except OSError:
                pass

    atexit.register(cleanup)


def _login():
    """Interactively log in and return a session key.

    Prompts for the email and master password here; `bw login` prompts for a
    two-step code itself when the account needs one. The password is passed to
    bw through the child process environment, never argv.
    """
    if not sys.stdin.isatty():
        raise BwError(
            "not logged in to Bitwarden and no terminal is available for "
            "login; run 'bw login' manually or provide BW_SESSION"
        )

    sys.stderr.write("Bitwarden login required.\n")
    sys.stderr.write("Bitwarden email: ")
    sys.stderr.flush()
    email = sys.stdin.readline().strip()
    if not email:
        raise BwError("no email supplied")

    password = getpass.getpass("Bitwarden master password: ")
    if not password:
        raise BwError("no master password supplied")

    env = dict(os.environ)
    env["BW_LOGIN_PASSWORD"] = password

    # Keep the script's stdout clean: bw's own output goes to the terminal.
    login = subprocess.run(
        ["bw", "login", email, "--passwordenv", "BW_LOGIN_PASSWORD"],
        env=env,
        stdout=2,
    )
    if login.returncode != 0:
        raise BwError("bw login failed")

    # Login leaves the vault locked; unlock with the same password.
    unlock = subprocess.run(
        ["bw", "unlock", "--raw", "--passwordenv", "BW_LOGIN_PASSWORD"],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    if unlock.returncode != 0:
        raise BwError("login succeeded but unlocking the vault failed")
    session = unlock.stdout.strip()
    if not session:
        raise BwError("bw unlock returned no session")
    return session


def ensure_session(cache_path=None):
    """Return a working BW_SESSION, unlocking at most once per process."""
    global _session
    if _session:
        return _session

    if shutil.which("bw") is None:
        raise BwError("Bitwarden CLI not found; install it and run 'bw login'")

    try:
        state = json.loads(_run_bw("status").stdout).get("status")
    except json.JSONDecodeError as exc:
        raise BwError("could not parse `bw status` output") from exc
    use_cache = os.environ.get("BW_NO_CACHE") != "1"
    if cache_path is None:
        cache_path = _default_cache_path()

    if state == "unauthenticated":
        session = _login()
        _session = session
        if use_cache and cache_path:
            _write_private(cache_path, f"{session}\n{int(time.time())}\n")
        _maybe_lock_on_exit(cache_path if use_cache else None)
        return session

    candidates = [os.environ.get("BW_SESSION", "")]
    if use_cache and cache_path:
        candidates.append(_read_cache(cache_path, _cache_ttl()))

    for session in candidates:
        if session and _session_is_valid(session):
            _session = session
            _maybe_lock_on_exit(cache_path if use_cache else None)
            return session

    # Ask for the master password once. getpass writes its prompt to the
    # terminal and does not echo; the password reaches bw through the
    # environment, never argv.
    try:
        password = getpass.getpass("Bitwarden master password: ")
    except (EOFError, KeyboardInterrupt):
        raise BwError("no master password supplied") from None
    if not password:
        raise BwError("no master password supplied")

    proc = _run_bw(
        "unlock",
        "--raw",
        "--passwordenv",
        "BW_MASTER_PASSWORD",
        extra_env={"BW_MASTER_PASSWORD": password},
    )
    session = proc.stdout.strip()
    if not session:
        raise BwError("bw unlock returned no session")
    _session = session

    if use_cache and cache_path:
        _write_private(cache_path, f"{session}\n{int(time.time())}\n")
    _maybe_lock_on_exit(cache_path if use_cache else None)
    return session


def _write_private(path, text):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _resolve_item(item):
    """Resolve an item name or id to an id; fail clearly on duplicates."""
    if item in _item_ids:
        return _item_ids[item]

    proc = _run_bw("get", "item", item, check=False)
    if proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("id"):
            _item_ids[item] = data["id"]
            return data["id"]

    listing = _run_bw("list", "items", "--search", item, check=False)
    try:
        items = json.loads(listing.stdout or "[]")
    except json.JSONDecodeError:
        items = []

    exact = [
        candidate
        for candidate in items
        if isinstance(candidate, dict) and candidate.get("name") == item
    ]
    if len(exact) == 1:
        _item_ids[item] = exact[0]["id"]
        return exact[0]["id"]

    matches = [
        candidate
        for candidate in items
        if isinstance(candidate, dict) and candidate.get("id")
    ]
    if not exact and len(matches) == 1:
        # A single search hit is unambiguous even if the name differs slightly.
        _item_ids[item] = matches[0]["id"]
        return matches[0]["id"]

    candidates = exact or matches
    if candidates:
        lines = "\n".join(
            f"  {c.get('id')}  {c.get('name')}  "
            f"[{(c.get('login') or {}).get('username') or '-'}]"
            for c in candidates
        )
        reason = (
            f"multiple Bitwarden items named '{item}'"
            if exact
            else f"multiple Bitwarden items match '{item}'"
        )
        raise BwError(
            f"{reason}; put the id you want in BW_SECRETS instead of the name:"
            f"\n{lines}"
        )

    detail = (proc.stderr or "").strip()
    raise BwError(detail or f"Bitwarden item '{item}' not found")


def _sync_once():
    """Refresh the local vault copy at most once per process."""
    global _synced, _sync_error
    if _synced:
        return False
    _synced = True
    proc = _run_bw("sync", check=False)
    if proc.returncode != 0:
        _sync_error = (
            (proc.stderr or proc.stdout or "").strip() or "bw sync failed"
        )
        return False
    return True


def _read_field(item_id, item, field):
    if field in BUILTIN_FIELDS:
        value = _run_bw("get", field, item_id).stdout.rstrip("\n")
        if not value:
            raise BwError(f"field '{field}' is empty on item '{item}'")
        return value

    try:
        data = json.loads(_run_bw("get", "item", item_id).stdout)
    except json.JSONDecodeError as exc:
        raise BwError(f"could not parse item JSON for '{item}'") from exc

    fields = [
        f for f in data.get("fields") or [] if isinstance(f, dict)
    ]
    available = [f.get("name") for f in fields if f.get("name")]
    wanted = field.strip().lower()

    def find(predicate):
        return [f for f in fields if predicate((f.get("name") or "").strip())]

    matches = find(lambda name: name == field.strip())
    if not matches:
        matches = find(lambda name: name.lower() == wanted)
    if not matches:
        # Tolerate legacy names such as "Jira user1 PAT" for field "PAT".
        matches = find(lambda name: name.lower().endswith(" " + wanted))
    if len(matches) == 1:
        value = matches[0].get("value") or ""
        if value:
            return value

    listed = ", ".join(available) if available else "none"
    raise BwError(
        f"no field '{field}' on '{item}' "
        f"(custom fields: {listed}; built-in: username, password, totp, notes, uri)"
    )


def credential(item, field="password"):
    """Return one credential value from the Bitwarden vault (memoized)."""
    key = (item, field)
    if key in _values:
        return _values[key]

    ensure_session()
    item_id = _resolve_item(item)

    try:
        value = _read_field(item_id, item, field)
    except BwError as exc:
        # A change in the web vault is invisible until the CLI syncs.
        if not _sync_once():
            if _sync_error:
                raise BwError(
                    f"{exc}\n`bw sync` also failed: {_sync_error}"
                ) from None
            raise
        value = _read_field(item_id, item, field)

    _values[key] = value
    return value


def get_secret(name):
    """Fetch a named secret from Bitwarden using the SECRETS mapping."""
    try:
        item, field = SECRETS[name]
    except KeyError:
        raise BwError(f"no Bitwarden mapping for {name!r}") from None
    return credential(item, field)


def main():
    usage = (
        "usage: bw_credentials.py <secret-name> | --list | --fields <item> "
        "| --sync | --version"
    )
    args = sys.argv[1:]
    if not args:
        print(usage, file=sys.stderr)
        return 1

    if args[0] in ("-V", "--version"):
        print(__version__)
        return 0

    if args[0] == "--sync":
        try:
            ensure_session()
        except BwError as exc:
            print(f"bw_credentials: {exc}", file=sys.stderr)
            return 1
        proc = _run_bw("sync", check=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            print(f"bw_credentials: bw sync failed: {detail}", file=sys.stderr)
            return 1
        print("Bitwarden vault synced.")
        return 0

    if args[0] in ("-l", "--list"):
        for name, (item, field) in SECRETS.items():
            print(f"{name}\t{item}\t{field}")
        return 0

    if args[0] == "--fields":
        if len(args) != 2:
            print(usage, file=sys.stderr)
            return 1
        try:
            ensure_session()
            item_id = _resolve_item(args[1])
            data = json.loads(_run_bw("get", "item", item_id).stdout)
        except (BwError, json.JSONDecodeError) as exc:
            print(f"bw_credentials: {exc}", file=sys.stderr)
            return 1
        login = data.get("login") or {}
        print(f"item:     {data.get('name', '?')}  ({item_id})")
        if login.get("username"):
            print(f"username: {login['username']}")
        names = [
            f.get("name") for f in data.get("fields") or [] if f.get("name")
        ]
        print("built-in: username password totp notes uri")
        print("custom:  " + (" ".join(names) if names else "(none)"))
        return 0

    if len(args) != 1:
        print(usage, file=sys.stderr)
        return 1

    try:
        print(get_secret(args[0]))
    except BwError as exc:
        print(f"bw_credentials: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
