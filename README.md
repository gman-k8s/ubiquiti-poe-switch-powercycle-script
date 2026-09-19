# ubiquiti-poe-switch-powercycle-script

Python-Script fuer einen self-hosted UniFi Network Application Controller
(z.B. als Home Assistant Add-on, keine UniFi-OS-Console). Prueft per Ping,
ob ein Geraet hinter einem PoE-Switch-Port erreichbar ist, und loest bei
Ausfall automatisch einen Power-Cycle des Ports aus. Fuer Cronjob gedacht.

## Voraussetzungen

- Linux, Python 3.9+ mit `venv`-Modul (Standard bei den meisten Distros;
  auf Debian/Ubuntu ggf. `apt install python3-venv`)
- `ping` im PATH (Standard auf jeder Linux-Distro)

## 1. Lokalen Service-Account im Controller anlegen

UniFi Network Application → Settings → Admins & Users → Add Admin.

- Lokaler Account (kein Ubiquiti-Cloud-SSO, kein 2FA-Zwang)
- Rolle: "Limited Admin" reicht (Geraete-Steuerung, kein Netzwerk-Redesign)
- Nur fuer die betreffende Site freigeben
- Passwort in einen Passwortmanager, nicht in Klartext irgendwo ablegen

## 2. Setup

```bash
git clone <repo-url>
cd ubiquiti-poe-switch-powercycle-script
./setup-venv.sh          # legt .venv an, installiert Deps aus requirements.txt
cp .env.example .env
$EDITOR .env              # Controller-URL, Zugangsdaten, Zielgeraet eintragen
```

`.env` enthaelt Zugangsdaten und wird von Git ignoriert (siehe `.gitignore`).
Committed werden nur `.env.example` (Template) und `requirements.txt` (Versions-Pin).

**Achtung `UNIFI_SITE`:** case-sensitive Short-Name aus der Controller-URL
(`.../manage/site/<name>/...`), nicht der Display-Name aus den Settings
(z.B. "Default" als Anzeigename != `default` als Site-Kurzname → 401 beim
Login).

`SWITCH_IDENTIFIER` ist ein Substring, der im `name` oder `model` des
Switches in `/api/s/<site>/stat/device` vorkommt (z.B. `USW-Pro-24` oder
ein von dir vergebener Name) — die MAC-Adresse wird darueber automatisch
ermittelt, nicht hardcoden.

## 3. Manuell testen

```bash
./poe_powercycle.py
echo $?
```

Das Script re-execed sich beim Start selbst in `.venv/bin/python3`
(die Shebang-Zeile kann wegen des Kernel-Limits fuer Shebangs, 127
Zeichen, nicht dynamisch auf `.venv` verweisen — der Re-Exec passiert
deshalb als erste Zeilen im Script). Fehlt `.venv`, bricht das Script
mit einem Hinweis auf `./setup-venv.sh` ab.

Alle `.env`-Werte lassen sich auch per Parameter uebergeben (praktisch
fuer weitere Ports/Switches ohne zweite `.env`):

```bash
./poe_powercycle.py --target-ip 192.168.100.21 --target-port-idx 7 \
    --switch-identifier "USW-Pro-24"
```

CLI-Parameter haben Vorrang vor Umgebungsvariablen, die wiederum Vorrang
vor Werten aus der `.env`-Datei haben. Fehlt ein Pflichtwert in beiden
Quellen, bricht das Script mit einer Liste der fehlenden Parameter ab
(Exit-Code 6).

## 4. Exit-Codes (fuer Cron-Auswertung)

| Code | Bedeutung |
|------|-----------|
| 0 | Ping OK, nichts unternommen |
| 1 | Ping down, aber Cooldown aktiv, kein Cycle ausgeloest |
| 2 | Ping down, Power-Cycle ausgeloest |
| 3 | Auth-Fehler gegen Controller (Login fehlgeschlagen) |
| 4 | API-/Netzwerkfehler gegen Controller, oder Switch nicht gefunden |
| 5 | Andere Instanz laeuft bereits (Lock gehalten), sauber uebersprungen |
| 6 | Konfigurationsfehler (Pflichtparameter fehlt) |

Logs (Zeitstempel, Aktion, Ergebnis) landen in der Datei aus `LOG_FILE`
(Default `./poe_powercycle.log`).

## 5. Cronjob einrichten

```bash
crontab -e
```

```cron
*/5 * * * * cd /pfad/zu/ubiquiti-poe-switch-powercycle-script && ./poe_powercycle.py >> /pfad/zu/cron.log 2>&1
```

Lock (`state/<switch>_<port>.lock`) und Cooldown-Timestamp
(`state/<switch>_<port>.lastcycle`) liegen in `STATE_DIR` (Default
`./state`, von Git ignoriert) und sind pro Switch+Port getrennt — mehrere
Cron-Zeilen fuer unterschiedliche Ports blockieren sich also nicht
gegenseitig. Ueberlappende Laeufe fuer denselben Port werden ueber
`flock` sauber uebersprungen (Exit-Code 5).

## Dateistruktur

```
poe_powercycle.py    Hauptscript
requirements.txt       Dependencies mit fixierten Versionen (requests + Transitive)
setup-venv.sh           Legt .venv per 'python3 -m venv' + pip an
.env.example            Config-Template (Controller, Credentials, Zielgeraet)
.env                    Echte Werte, NICHT in Git (siehe .gitignore)
state/                   Lock- und Cooldown-Dateien, NICHT in Git
```

## Versionen aktualisieren

`requirements.txt` von Hand anpassen (z.B. `requests` auf neue Version),
dann:

```bash
rm -rf .venv
./setup-venv.sh
```
