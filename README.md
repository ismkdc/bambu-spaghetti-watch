# Bambu Spaghetti Watch

A macOS menu bar app that watches a Bambu Lab printer, runs Obico's failure
model locally, and **stops** the print when the failure signal is sustained.

No Home Assistant, no extra server, no Obico account. Status and camera go
through Bambu's own cloud session; signed pause/resume/stop use the printer's
LAN address through the same plugin, because the cloud client does not expose
that publish. Detection runs in-process in ~30 ms.

## What it does

- Reads the printer's status from Bambu's cloud session (the networking plugin
  that ships with Studio)
- Pulls one frame every 5 seconds while a job is running, from Bambu's cloud
  live view (`libBambuSource`), decoded with ffmpeg
- Runs the current Obico model (2025-12 darknet weights) in-process with
  `cv2.dnn` — ~27 ms per 1080p frame on an M-series Mac
- Smooths the per-frame score the way Obico does (EWM + rolling baseline), so a
  single odd-looking frame cannot trigger anything
- On a sustained failure: a notification and a **stop** command, signed by the
  same plugin Studio uses

The model was checked against real labelled failure frames: all seven
`spaghetti` fixtures score 0.21–0.42 objectness, all five healthy frames score
0.00. See `tests/fixtures/print_failure/ATTRIBUTION.md`.

## How it works

| Layer | Transport | Used for |
|---|---|---|
| Status | Bambu cloud session | `gcode_state`, progress, job name |
| Control | printer LAN, signed by the plugin | pause / resume / stop |
| Camera | Bambu cloud live view (TUTK) | one JPEG every 5 s |

The app loads the networking plugin that Bambu Studio installs and signs in with
the user's existing Studio session. Status and camera work from any network.
Signed control needs the printer's LAN address (or a VPN) and the macOS Local
Network permission — see [INSTALL.md](INSTALL.md) step 10b.

Control commands are signed by the plugin because firmware since 2025 rejects
unsigned commands with `84033543 mqtt message verify failed`. The vendored
`bambu-headless` bridge performs that bring-up over the printer's local session;
the app never implements the signing itself.

Nothing from Bambu is redistributed. The app loads the plugin and libraries
already installed by Bambu Studio.

## Requirements

- macOS on Apple Silicon or Intel
- Python 3.12 (`/opt/homebrew/bin/python3.12`)
- Xcode Command Line Tools (`xcode-select --install`)
- `ffmpeg` and `nlohmann-json` (`brew install ffmpeg nlohmann-json`)
- Bambu Studio installed and signed in, with the printer **bound to your Bambu
  account**

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

Normally you configure nothing. The app reads the printer serial from Bambu
Studio's config and uses your Studio cloud session:

```
printer   : 01S00A123456789 (cloud)
camera    : cloud
```

A config file is only needed to override something:

`~/.config/bambu-spaghetti-watch/config.json`:

```json
{
  "serial": "01S00A123456789",
  "interval_sec": 5,
  "failure_action": "stop",
  "sensitivity": "medium"
}
```

| Key | Values | Default |
|---|---|---|
| `failure_action` | `stop`, `pause`, `notify`, `none` | `stop` |
| `sensitivity` | `low`, `medium`, `high` | `medium` |
| `interval_sec` | seconds between frames | `5` |
| `serial` | printer serial (only needed with several printers) | from Studio |
| `model` | `auto` or a model code | `auto` |

The default `stop` is deliberate: the app is meant to save a print that is
already failing. If you want to watch a few clean prints first, set
`failure_action` to `notify` (or `pause`) and change it later.

## First run

```bash
.venv/bin/bambu-watch --probe     # cloud session, camera and model, one round trip
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

The agent uses `RunAtLoad` and `KeepAlive`, so it comes back at login and after
a crash. It connects to the cloud on its own and shows the icon before the
first report arrives.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `printer error: no printer saved in Bambu Studio` | Studio has no printer | add the printer in Bambu Studio and sign in |
| `cloud     : no status received` | Studio session expired, or the printer is offline | open Bambu Studio once and confirm the printer is online |
| `camera error: ... bridge ...` | `bridge/` was not built or moved | run `./bridge/build.sh` |
| `cloud camera could not mint a live-view URL` | Studio session expired, printer offline, or the plugin ABI changed | open Bambu Studio once, re-run `--probe` |
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
- Executable-path shim and the signed-control bridge:
  [danielyaa5/bambu-headless](https://github.com/danielyaa5/bambu-headless)
- Protocol details: [bambuddy](https://github.com/maziggy/bambuddy) and
  [ClusterM/open-bamboo-networking](https://github.com/ClusterM/open-bamboo-networking)

The smoothing layer is ported from AGPL-3.0 code. This project is therefore
AGPL-3.0 (see [LICENSE](LICENSE)): if you distribute a build, you must publish
the source.
