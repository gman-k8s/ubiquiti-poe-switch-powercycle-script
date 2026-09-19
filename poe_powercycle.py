#!/usr/bin/env python3
"""
Power-cycle eines UniFi-PoE-Switch-Ports, wenn das dahinterhaengende
Geraet nicht mehr pingbar ist.

Fuer Cronjob gedacht. Exit-Codes siehe README.md.
"""

import os
import sys

# Shebang kann wegen Kernel-Limit (BINPRM_BUF_SIZE, 127 Zeichen) nicht
# dynamisch auf ".venv" verweisen. Deshalb hier, vor jedem Fremd-Import:
# in .venv re-execen, oder mit Hinweis abbrechen, falls .venv fehlt.
_here = os.path.dirname(os.path.abspath(__file__))
_venv_py = os.path.join(_here, ".venv", "bin", "python3")
if not os.path.exists(_venv_py):
    sys.stderr.write(
        f"FEHLER: .venv fehlt unter {_here}/.venv - "
        "bitte zuerst ./setup-venv.sh ausfuehren.\n"
    )
    sys.exit(1)
if os.path.realpath(sys.executable) != os.path.realpath(_venv_py):
    os.execv(_venv_py, [_venv_py] + sys.argv)

import argparse
import fcntl
import logging
import pathlib
import re
import subprocess
import time

import requests
import urllib3

# (dest, ENV_KEY, type, default, required)
CONFIG_FIELDS = [
    ("controller_url", "CONTROLLER_URL", str, None, True),
    ("verify_ssl", "CONTROLLER_VERIFY_SSL", bool, False, False),
    ("site", "UNIFI_SITE", str, "default", False),
    ("username", "UNIFI_USERNAME", str, None, True),
    ("password", "UNIFI_PASSWORD", str, None, True),
    ("switch_identifier", "SWITCH_IDENTIFIER", str, None, True),
    ("target_ip", "TARGET_IP", str, None, True),
    ("target_port_idx", "TARGET_PORT_IDX", int, None, True),
    ("ping_count", "PING_COUNT", int, 3, False),
    ("ping_timeout", "PING_TIMEOUT", int, 2, False),
    ("cooldown_seconds", "COOLDOWN_SECONDS", int, 300, False),
    ("log_file", "LOG_FILE", str, "./poe_powercycle.log", False),
    ("state_dir", "STATE_DIR", str, "./state", False),
]

EXIT_OK = 0
EXIT_COOLDOWN_SKIP = 1
EXIT_POWERCYCLED = 2
EXIT_AUTH_ERROR = 3
EXIT_API_ERROR = 4
EXIT_LOCKED_SKIP = 5
EXIT_CONFIG_ERROR = 6


class ConfigError(Exception):
    pass


def load_env_file(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        values[key] = val
    return values


def _cast(value, typ):
    if isinstance(value, typ) and not (typ is int and isinstance(value, bool)):
        return value
    if typ is bool:
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if typ is int:
        return int(value)
    return str(value)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Ping-Check + Power-Cycle eines UniFi-PoE-Switch-Ports."
    )
    parser.add_argument(
        "--env-file", default=".env",
        help="Pfad zur .env-Datei (Default: ./.env)",
    )
    for dest, _env_key, typ, _default, _required in CONFIG_FIELDS:
        flag = "--" + dest.replace("_", "-")
        if typ is bool:
            parser.add_argument(flag, dest=dest, action="store_true", default=None)
            parser.add_argument(
                "--no-" + dest.replace("_", "-"), dest=dest, action="store_false", default=None
            )
        else:
            parser.add_argument(flag, dest=dest, default=None)
    return parser


def resolve_config(args, env_values):
    config = {}
    missing = []
    for dest, env_key, typ, default, required in CONFIG_FIELDS:
        cli_val = getattr(args, dest, None)
        if cli_val is not None:
            config[dest] = _cast(cli_val, typ)
            continue
        raw = os.environ.get(env_key)
        if raw is None:
            raw = env_values.get(env_key)
        if raw is not None and raw != "":
            config[dest] = _cast(raw, typ)
            continue
        if default is not None:
            config[dest] = default
            continue
        if required:
            missing.append((dest, env_key))
        else:
            config[dest] = None
    if missing:
        lines = [
            f"  --{d.replace('_', '-')}   oder   {e}=... in .env"
            for d, e in missing
        ]
        raise ConfigError("Fehlende Pflicht-Parameter:\n" + "\n".join(lines))
    return config


def setup_logging(log_file):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stderr),
        ],
    )


def sanitize(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def ping_ok(ip, count, timeout):
    result = subprocess.run(
        ["ping", "-c", str(count), "-W", str(timeout), ip],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _raise_for_status_verbose(resp):
    if resp.status_code >= 400:
        body = resp.text.strip()[:500]
        logging.error(
            "Controller antwortet %s auf %s: %s",
            resp.status_code, resp.url, body or "(leerer Body)",
        )
    resp.raise_for_status()


class UnifiSession:
    def __init__(self, base_url, username, password, verify_ssl, site):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.site = site
        self.session = requests.Session()
        self.session.verify = verify_ssl
        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def login(self):
        resp = self.session.post(
            f"{self.base_url}/api/login",
            json={"username": self.username, "password": self.password},
            timeout=10,
        )
        _raise_for_status_verbose(resp)

    def request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, timeout=15, **kwargs)
        if resp.status_code in (401, 403):
            self.login()
            resp = self.session.request(method, url, timeout=15, **kwargs)
        _raise_for_status_verbose(resp)
        return resp


def find_switch_mac(sess, identifier):
    resp = sess.request("GET", f"/api/s/{sess.site}/stat/device")
    devices = resp.json().get("data", [])
    ident_lower = identifier.lower()
    for dev in devices:
        name = (dev.get("name") or "").lower()
        model = (dev.get("model") or "").lower()
        if ident_lower in name or ident_lower in model:
            mac = dev.get("mac")
            if mac:
                return mac
    raise LookupError(f"Kein Switch gefunden fuer SWITCH_IDENTIFIER '{identifier}'")


def power_cycle(sess, mac, port_idx):
    payload = {"mac": mac, "port_idx": port_idx, "cmd": "power-cycle"}
    sess.request("POST", f"/api/s/{sess.site}/cmd/devmgr", json=payload)


def run(cfg):
    logging.info(
        "Start Check: target_ip=%s port_idx=%s switch=%s",
        cfg["target_ip"], cfg["target_port_idx"], cfg["switch_identifier"],
    )

    if ping_ok(cfg["target_ip"], cfg["ping_count"], cfg["ping_timeout"]):
        logging.info("Ping OK (%s), nichts zu tun", cfg["target_ip"])
        return EXIT_OK

    logging.warning("Ping fehlgeschlagen fuer %s", cfg["target_ip"])

    state_dir = pathlib.Path(cfg["state_dir"])
    state_dir.mkdir(parents=True, exist_ok=True)
    target_key = sanitize(f"{cfg['switch_identifier']}_{cfg['target_port_idx']}")
    lock_path = state_dir / f"{target_key}.lock"
    cooldown_path = state_dir / f"{target_key}.lastcycle"

    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logging.info("Andere Instanz laeuft bereits fuer %s, skip", target_key)
        os.close(lock_fd)
        return EXIT_LOCKED_SKIP

    try:
        if cooldown_path.exists():
            try:
                last = float(cooldown_path.read_text().strip() or "0")
            except ValueError:
                last = 0.0
            elapsed = time.time() - last
            if elapsed < cfg["cooldown_seconds"]:
                logging.info(
                    "Cooldown aktiv fuer %s (%.0fs verbleiben), skip",
                    target_key, cfg["cooldown_seconds"] - elapsed,
                )
                return EXIT_COOLDOWN_SKIP

        try:
            sess = UnifiSession(
                cfg["controller_url"], cfg["username"], cfg["password"],
                cfg["verify_ssl"], cfg["site"],
            )
            sess.login()
            mac = find_switch_mac(sess, cfg["switch_identifier"])
            power_cycle(sess, mac, cfg["target_port_idx"])
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (401, 403):
                logging.error("Auth-Fehler gegen Controller: %s", exc)
                return EXIT_AUTH_ERROR
            logging.error("HTTP-Fehler vom Controller: %s", exc)
            return EXIT_API_ERROR
        except requests.exceptions.RequestException as exc:
            logging.error("Verbindungsfehler zum Controller: %s", exc)
            return EXIT_API_ERROR
        except LookupError as exc:
            logging.error("%s", exc)
            return EXIT_API_ERROR

        cooldown_path.write_text(str(time.time()))
        logging.info(
            "Power-Cycle ausgeloest: Port %s, MAC %s", cfg["target_port_idx"], mac
        )
        return EXIT_POWERCYCLED
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main():
    parser = build_parser()
    args = parser.parse_args()

    env_values = load_env_file(pathlib.Path(args.env_file))

    try:
        cfg = resolve_config(args, env_values)
    except ConfigError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    setup_logging(cfg["log_file"])
    return run(cfg)


if __name__ == "__main__":
    sys.exit(main())
