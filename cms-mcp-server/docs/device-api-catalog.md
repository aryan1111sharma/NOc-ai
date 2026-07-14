# Device-API catalog — old NOC (ChargerDetails) reference

Captured live 2026-07-14 from `GetAllApisByChargerID` for a representative
charger (`ab-int64-00033`). **`ApiID` is per-charger — resolve by charger, do
not hard-code.** This is the full set of commands the old NOC can run on a
charger via `ExecuteApi`. See `docs/superpowers/specs/2026-07-14-cms-device-command-tools-design.md`.

**Total: 129 APIs** — 8 DELETE, 60 GET, 54 POST, 7 PUT.

Legend: 🟢 GET = read-only diagnostic · 🔴 POST/PUT/DELETE = write.
`[IN]` = input/body required. The **gated write allow-list** (only these are
runnable via `cms_run_device_action`) is marked ✅; everything else write is 🚫 hard-blocked.


## Category 1 — Core device

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 12 | GET | `controllers` | Controllers |  | 🟢 read |
| 90 | GET | `facts` | Facts |  | 🟢 read |
| 1 | GET | `id` | ID |  | 🟢 read |
| 160 | GET | `macaddress` | Get MacAddress |  | 🟢 read |
| 11 | GET | `outlets` | Outlets |  | 🟢 read |
| 7 | GET | `release` | Release |  | 🟢 read |
| 166 | GET | `state` | State |  | 🟢 read |
| 163 | GET | `swagger` | Swagger |  | 🟢 read |
| 162 | GET | `swagger.json` | Swagger.json |  | 🟢 read |
| 161 | GET | `swagger.yml` | Swagger.yml |  | 🟢 read |
| 8 | GET | `version` | Version |  | 🟢 read |
| 165 | POST | `controllers` | Post controllers | ✔ | 🔴🚫 blocked |
| 6 | POST | `controllers/{scripts}/api/outlets/ccs/restartStack` | RestartStack |  | 🔴✅ gated |
| 164 | POST | `outlets` | Post outlets | ✔ | 🔴🚫 blocked |
| 154 | POST | `reset` | Reset |  | 🔴✅ gated |
| 159 | POST | `state` | Post error object | ✔ | 🔴🚫 blocked |

## Category 2 — Scripts

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 33 | DELETE | `scripts/active/{script}` | Delete Active scripts |  | 🔴🚫 blocked |
| 30 | DELETE | `scripts/installed/{script}` | Delete installed scripts |  | 🔴🚫 blocked |
| 20 | GET | `scripts` | Scripts |  | 🟢 read |
| 21 | GET | `scripts/active` | Active Scripts |  | 🟢 read |
| 31 | GET | `scripts/active/{script}` | Active scripts |  | 🟢 read |
| 24 | GET | `scripts/installed` | Installed Scripts |  | 🟢 read |
| 25 | GET | `scripts/installed/{script}` | Installed Scripts  |  | 🟢 read |
| 22 | POST | `scripts/active` | Post Active Scripts | ✔ | 🔴🚫 blocked |
| 26 | POST | `scripts/installed/{script}` | Post installed scripts | ✔ | 🔴🚫 blocked |
| 32 | PUT | `scripts/active/{script}` | Put Active scripts | ✔ | 🔴🚫 blocked |
| 29 | PUT | `scripts/installed/{script}` | Put installed scripts | ✔ | 🔴🚫 blocked |

## Category 3 — State/updates

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 38 | GET | `outlet/{number}/updates` | Outlets updates |  | 🟢 read |
| 36 | GET | `outlets/{number}/state` | Outlets state |  | 🟢 read |
| 34 | GET | `state` | State |  | 🟢 read |
| 35 | GET | `updates` | Updates |  | 🟢 read |
| 37 | POST | `outlets/{number}/state` | Post outlet state | ✔ | 🔴🚫 blocked |

## Category 4 — Outlets / charging

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 46 | DELETE | `outlets/{number}/test` | Delete test outlets |  | 🔴🚫 blocked |
| 39 | GET | `outlets/{number}` | Charge Outlets |  | 🟢 read |
| 40 | GET | `outlets/{number}/state` | Outlets state |  | 🟢 read |
| 42 | GET | `outlets/{number}/updates` | Outlets updates |  | 🟢 read |
| 48 | POST | `outlets/{number}/auth` | Post Authorize | ✔ | 🔴🚫 blocked |
| 58 | POST | `outlets/{number}/cert` | Install certificate | ✔ | 🔴🚫 blocked |
| 55 | POST | `outlets/{number}/e-stop` | Trigger e-stop | ✔ | 🔴🚫 blocked |
| 56 | POST | `outlets/{number}/evseid` | Set EVSEID | ✔ | 🔴🚫 blocked |
| 53 | POST | `outlets/{number}/out_of_order` | Post Out of order | ✔ | 🔴🚫 blocked |
| 54 | POST | `outlets/{number}/powercap` | Set Powercap | ✔ | 🔴🚫 blocked |
| 41 | POST | `outlets/{number}/state` | Post outlets state | ✔ | 🔴🚫 blocked |
| 52 | POST | `outlets/{number}/stop` | Post Stop Charge | ✔ | 🔴✅ gated |
| 45 | POST | `outlets/{number}/test` | Post outlets test | ✔ | 🔴🚫 blocked |
| 43 | POST | `outlets/{number}/test/override` | Post test override | ✔ | 🔴🚫 blocked |
| 47 | POST | `outlets/{number}/unplug` | Post Unplug |  | 🔴🚫 blocked |
| 157 | POST | `services/secc/api/outlets/{outletID}/coap/unlock` | Unlock the given outletID  |  | 🔴🚫 blocked |

## Category 6 — Controllers

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 60 | GET | `controllers/{seccid}/api/network/configureip` | Get controller's IP |  | 🟢 read |
| 63 | GET | `controllers/{seccid}/api/stack-selection` | Get Active Stack |  | 🟢 read |
| 61 | POST | `controllers/{seccid}/api/network/configureip` | Set IP for LE | ✔ | 🔴🚫 blocked |
| 64 | POST | `controllers/{seccid}/api/stack-selection` | Switch DC Stack | ✔ | 🔴🚫 blocked |
| 62 | POST | `controllers/{seccid}/api/system/reboot` | Reboot controller |  | 🔴✅ gated |

## Category 9 — RFID

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 138 | GET | `services/rfid/cardDetector` | Read if card is detected |  | 🟢 read |
| 139 | GET | `services/rfid/errors` | Read if error is occured |  | 🟢 read |
| 156 | GET | `services/rfid/idtag` | Get the current RFID tag |  | 🟢 read |
| 142 | GET | `services/rfid/outletId` | Get current outlet |  | 🟢 read |
| 136 | GET | `services/rfid/reader` | Read RFID status |  | 🟢 read |
| 137 | POST | `services/rfid/clear` | Clear the received RFID tag before the timeout |  | 🔴✅ gated |
| 140 | POST | `services/rfid/idtag` | Set specific Id Tag | ✔ | 🔴🚫 blocked |
| 141 | POST | `services/rfid/outletId` | Set Outlet | ✔ | 🔴🚫 blocked |

## Category 10 — LifeC

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 65 | POST | `services/lifec/alert` | LifeC alerts | ✔ | 🔴🚫 blocked |

## Category 11 — LED control

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 104 | GET | `services/led-ctrl/colors` | Read current color |  | 🟢 read |
| 106 | GET | `services/led-ctrl/effects` | Read effects |  | 🟢 read |
| 105 | POST | `services/led-ctrl/colors` | Modify color setting | ✔ | 🔴🚫 blocked |
| 107 | POST | `services/led-ctrl/effects` | Modify effects | ✔ | 🔴🚫 blocked |

## Category 12 — GPIO / PWM

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 114 | DELETE | `services/gpio/gpio/unexport` | Unexport GPIO |  | 🔴🚫 blocked |
| 119 | DELETE | `services/gpio/pwm/{pwmChip}` | Unexport PWM chip | ✔ | 🔴🚫 blocked |
| 110 | GET | `services/gpio/gpio/direction/{gpioNum}` | Read direction | ✔ | 🟢 read |
| 108 | GET | `services/gpio/gpio/value/{gpioNum}` | Read GPIO value | ✔ | 🟢 read |
| 115 | GET | `services/gpio/pwm` | PWM |  | 🟢 read |
| 116 | GET | `services/gpio/pwm/{pwmChip}` | Read PWM chip | ✔ | 🟢 read |
| 120 | GET | `services/gpio/pwm/{pwmChip}/{num}` | Exported PWM | ✔ | 🟢 read |
| 121 | GET | `services/gpio/pwm/{pwmChip}/{num}/{prop}` | PWM GPIO | ✔ | 🟢 read |
| 112 | POST | `services/gpio/gpio/direction/{gpioNum}` | Set direction | ✔ | 🔴🚫 blocked |
| 113 | POST | `services/gpio/gpio/export` | Export GPIO | ✔ | 🔴🚫 blocked |
| 109 | POST | `services/gpio/gpio/value/{gpioNum}` | Set GPIO value | ✔ | 🔴🚫 blocked |
| 117 | POST | `services/gpio/pwm/{pwmChip}` | Export PWM chip | ✔ | 🔴🚫 blocked |
| 122 | POST | `services/gpio/pwm/{pwmChip}/{num}/{prop}` | Set PWM GPIO | ✔ | 🔴🚫 blocked |
| 124 | POST | `services/gpio/setup/pwm/` | Setup PWM | ✔ | 🔴🚫 blocked |

## Category 13 — Display

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 102 | GET | `service/display/brightnessSensor` | Read brightness |  | 🟢 read |
| 97 | GET | `service/display/dimming` | Read dimming status |  | 🟢 read |
| 99 | GET | `service/display/dimming/level` | Get dimming level |  | 🟢 read |
| 98 | PUT | `service/display/dimming` | Set dimming ON/OFF | ✔ | 🔴🚫 blocked |
| 100 | PUT | `service/display/dimming/level` | Set minimum dimming level | ✔ | 🔴🚫 blocked |

## Category 14 — Meter / transactions

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 152 | GET | `services/meter/settings/ipAddress` | Read meter IP address |  | 🟢 read |
| 150 | GET | `services/meter/settings/time` | Read Time |  | 🟢 read |
| 143 | GET | `services/meter/status` | Read Latest transaction |  | 🟢 read |
| 144 | GET | `services/meter/transactions` | Read all transactions |  | 🟢 read |
| 148 | GET | `services/meter/transactions/{transactionId}` | Get transaction by ID | ✔ | 🟢 read |
| 145 | POST | `services/meter/transactions` | Start transaction | ✔ | 🔴🚫 blocked |
| 149 | POST | `services/meter/transactions/{transactionId}` | Stop transaction | ✔ | 🔴🚫 blocked |
| 153 | PUT | `services/meter/settings/ipAddress` | Set IP Address | ✔ | 🔴🚫 blocked |
| 151 | PUT | `services/meter/settings/time` | Synchronize time | ✔ | 🔴🚫 blocked |

## Category 15 — HMI

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 66 | GET | `hmi` | HMI |  | 🟢 read |
| 69 | GET | `hmi/config` | Display Config |  | 🟢 read |
| 68 | POST | `hmi` | Post HMI | ✔ | 🔴🚫 blocked |
| 70 | POST | `hmi/config` | Update display config | ✔ | 🔴🚫 blocked |
| 71 | PUT | `hmi/config` | Put Display config | ✔ | 🔴🚫 blocked |

## Category 16 — Docker apps

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 92 | DELETE | `docker-apps/compose/{id}` | Remove cluster | ✔ | 🔴🚫 blocked |
| 76 | DELETE | `docker-apps/containers/{id}` | Delete Container | ✔ | 🔴🚫 blocked |
| 80 | DELETE | `docker-apps/images/{id}` | Delete Image | ✔ | 🔴🚫 blocked |
| 81 | GET | `docker-apps/compose` | List of docker compose |  | 🟢 read |
| 83 | GET | `docker-apps/compose/{id}` | Cluster Definition | ✔ | 🟢 read |
| 72 | GET | `docker-apps/containers/json?all=true` | List Containers |  | 🟢 read |
| 77 | GET | `docker-apps/images/json?all=true` | List Images |  | 🟢 read |
| 79 | GET | `docker-apps/images/{id}/get` | Export Image | ✔ | 🟢 read |
| 82 | POST | `docker-apps/compose` | Upload Compose | ✔ | 🔴🚫 blocked |
| 91 | POST | `docker-apps/compose/{id}` | Modify docker compose | ✔ | 🔴🚫 blocked |
| 93 | POST | `docker-apps/compose/{id}/build` | Build cluster images | ✔ | 🔴🚫 blocked |
| 96 | POST | `docker-apps/compose/{id}/ps` | List cluster | ✔ | 🔴🚫 blocked |
| 95 | POST | `docker-apps/compose/{id}/stop` | Stop cluster | ✔ | 🔴🚫 blocked |
| 94 | POST | `docker-apps/compose/{id}/up` | Start cluster | ✔ | 🔴🚫 blocked |
| 73 | POST | `docker-apps/containers/create?name={name}` | Create Container | ✔ | 🔴🚫 blocked |
| 74 | POST | `docker-apps/containers/{id}/start` | Start Container | ✔ | 🔴🚫 blocked |
| 75 | POST | `docker-apps/containers/{id}/stop` | Stop Container | ✔ | 🔴🚫 blocked |
| 78 | POST | `docker-apps/images/load` | Import Image | ✔ | 🔴🚫 blocked |

## Category 17 — OCPP client

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 127 | GET | `ocpp-client/config` | Config of OCPP client |  | 🟢 read |
| 126 | GET | `ocpp-client/connectionstatus` | Status of client connection |  | 🟢 read |
| 155 | GET | `ocpp-client/logs/ocpp.log` | OCPP Logs |  | 🟢 read |
| 125 | GET | `ocpp-client/servicestatus` | Status of client service |  | 🟢 read |
| 134 | GET | `services/ocpp/localAuthList` | OCPP client local auth list |  | 🟢 read |
| 128 | POST | `ocpp-client/config` | Update OCPP config | ✔ | 🔴🚫 blocked |
| 132 | POST | `ocpp-client/restart` | Restart client service |  | 🔴✅ gated |
| 129 | POST | `ocpp-client/start` | Start client service |  | 🔴✅ gated |
| 130 | POST | `ocpp-client/stop` | Stop client service |  | 🔴✅ gated |
| 135 | POST | `services/ocpp/localAuthList` | Update Auth list | ✔ | 🔴🚫 blocked |

## Category 18 — State logs

| ApiID | Verb | API path | Name | In | Access |
|------:|------|----------|------|:--:|--------|
| 84 | GET | `controllers/1/api/outlets/1/log/state/full` | Full State Log-1 |  | 🟢 read |
| 89 | GET | `controllers/2/api/outlets/1/log/state/full` | Full State Log -2 |  | 🟢 read |