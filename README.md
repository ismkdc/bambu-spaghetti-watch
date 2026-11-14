# Bambu Spaghetti Watch

A macOS menu bar app that watches a Bambu Lab printer's **cloud live view**,
runs Obico's failure model locally, and can pause the print when the signal is
strong enough.

No Home Assistant, no extra server, no Obico account. The camera comes from
Bambu's own cloud live-view tunnel; detection runs in-process in ~30 ms.

## What it does

- Mints the cloud live-view URL with the networking plugin that ships with
  Bambu Studio, then reads frames from Bambu's own `libBambuSource`
- Pulls one frame every 5 seconds while the printer reports `RUNNING`
- Runs the current Obico model (2025-12 darknet weights) in-process with
  `cv2.dnn` — ~27 ms per 1080p frame on an M-series Mac
- Smooths the per-frame score the way Obico does (EWM + rolling baseline), so a
  single odd-looking frame cannot trigger anything
- On a sustained failure: notification, and optionally `pause`

The model was checked against real labelled failure frames: all seven
`spaghetti` fixtures score 0.21–0.42 objectness, all five healthy frames score
0.00. See `tests/fixtures/print_failure/ATTRIBUTION.md`.

## How it works

| Layer | Transport | Used for |
|---|---|---|
| Status / control | printer MQTT `:8883` | `gcode_state`, pause/resume/stop |
| Camera | Bambu cloud live-view (TUTK) | one JPEG every 5 s |

Control commands are signed by Studio's networking plugin through the vendored
[`bambu-headless`](https://github.com/danielyaa5/bambu-headless) bridge, because
firmware since 2025 rejects unsigned commands with `84033543 mqtt message verify
failed`.

The camera needs no printer-side live-view setting. The app asks Studio's plugin for a
live-view URL (the same ticket Bambu Handy uses) and hands it to
`libBambuSource`. It works locally and remotely alike.

Nothing from Bambu is redistributed. The app loads the plugin and libraries
already installed by Bambu Studio.

## Requirements

- macOS on Apple Silicon or Intel
- Python 3.12 (`/opt/homebrew/bin/python3.12`)
- Xcode Command Line Tools (`xcode-select --install`)
- `ffmpeg` and `nlohmann-json` (`brew install ffmpeg nlohmann-json`)
- Bambu Studio installed, signed in, with the printer **bound to your Bambu
  account** (the camera is Bambu's cloud live view; a printer that is not
  cloud-bound has no camera here)
- The printer reachable for status and control (directly or over VPN)

## Install

Step-by-step: **[INSTALL.md](INSTALL.md)**. Short version:

```bash
git clone https://github.com/ismkdc/bambu-spaghetti-watch.git
cd bambu-spaghetti-watch
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
./bridge/build.sh
curl -L -o src/bambu_spaghetti_watch/models/model-weights.darknet \
  https://www.obico.io/static_pub/ml-models/model-weights-ef79dacfd0051ab526f3002d5f5f9912.darknet
```

## Configure

Normally you configure nothing. At startup the app asks the local network for
Bambu printers over SSDP, then reads the access code for the matching serial from
Bambu Studio's own config:

```
printer   : 192.168.1.50 (01S00A123456789)
access    : d9******    (from ~/Library/Application Support/BambuStudio/BambuStudio.conf)
```

A config file is only needed to override something:

`~/.config/bambu-spaghetti-watch/config.json`:

```json
{
  "host": "192.168.1.50",
  "serial": "01S00A123456789",
  "access_code": "12345678",
  "interval_sec": 5,
  "failure_action": "notify",
  "sensitivity": "medium"
}
```

| Key | Values | Default |
|---|---|---|
| `failure_action` | `notify`, `pause`, `stop`, `none` | `notify` |
| `sensitivity` | `low`, `medium`, `high` | `medium` |
| `camera_mode` | `cloud` | `cloud` |
| `model` | `auto` or a model code | `auto` |

Start with `notify`. Thresholds are tuned for Obico's own cameras, not for your
printer's lighting; watch a few clean prints before letting it pause.

## First run

```bash
.venv/bin/bambu-watch --probe     # MQTT, camera and model, one round trip
.venv/bin/bambu-watch --once      # one detection step, prints the verdict
.venv/bin/bambu-watch             # the menu bar app
```

`--probe` is the only command that tells you which of the three layers is
broken. Run it first, always. The first camera frame takes ~20 s because the
cloud URL is minted on demand; later frames are ~2–3 s.

## Autostart

```bash
mkdir -p ~/Library/Logs/bambu-spaghetti-watch
cp launchd/io.github.bambu-spaghetti-watch.plist ~/Library/LaunchAgents/
# edit the three absolute paths first
launchctl load ~/Library/LaunchAgents/io.github.bambu-spaghetti-watch.plist
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no status received` | wrong access code, or the printer is not reachable locally | check the code on the printer screen |
| `no status received` as a discovery error | several printers answered SSDP | set `serial` in the config |
| `camera error: ... bridge ...` | `bridge/` was not built or moved | run `./bridge/build.sh` |
| `cloud camera could not mint a live-view URL` | Studio session expired, printer offline, not cloud-bound, or the plugin ABI changed | open Bambu Studio once, confirm the printer is online and bound to your account, re-run `--probe` |
| `cloud camera returned no frame` | the minted ticket aged out, or the tunnel was refused | delete `~/Library/Caches/bambu-watch/liveview_url.txt` and retry |
| Frequent false positives | thresholds tuned for different lighting | lower `sensitivity` |

## Tests

```bash
.venv/bin/python -m pytest -q
.venv/bin/python bridge/eval_detector.py   # score the labelled fixtures
```

The fixture test is skipped automatically when the model weights are not
installed.

## Credits and licence

- Detection model and decision maths: [Obico](https://github.com/TheSpaghettiDetective/obico-server)
- Signed control bridge and executable-path shim:
  [danielyaa5/bambu-headless](https://github.com/danielyaa5/bambu-headless)
- Protocol details: [bambuddy](https://github.com/maziggy/bambuddy) and
  [ClusterM/open-bamboo-networking](https://github.com/ClusterM/open-bamboo-networking)

The smoothing layer is ported from AGPL-3.0 code. This project is therefore
AGPL-3.0 (see [LICENSE](LICENSE)): if you distribute a build, you must publish
the source.
