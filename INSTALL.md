# Install — step by step

This is the long version of the quick start in the README. It assumes a fresh
Mac and a **P1S** already set up in Bambu Studio and bound to your Bambu
account.

You will need about 10 minutes and ~250 MB of disk (mostly the model weights).

---

## 1. Check macOS and the compiler

```bash
sw_vers
xcode-select -p
```

If `xcode-select` prints an error, install the Command Line Tools:

```bash
xcode-select --install
```

Accept the dialog and wait for it to finish.

## 2. Install Homebrew packages

If you do not have Homebrew:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then:

```bash
brew install python@3.12 ffmpeg nlohmann-json
```

> On an Intel Mac, Homebrew lives in `/usr/local`, so use
> `/usr/local/bin/python3.12` everywhere below.

Check:

```bash
/opt/homebrew/bin/python3.12 --version     # Python 3.12.x
ffmpeg -version | head -1
```

## 3. Prepare Bambu Studio

The app does **not** launch Bambu Studio, but it uses three things Studio
installs and one thing Studio stores:

1. `libbambu_networking.dylib` — the networking plugin
2. `libBambuSource.dylib` — the live-view library
3. `Contents/Resources/cert/slicer_base64.cer` — the TLS certificate
4. Your signed-in session and the printer's access code

So:

1. Install Bambu Studio.
2. Sign in to your Bambu account.
3. Add the P1S **and confirm it appears in your Bambu account** (cloud binding).
   The camera is Bambu's cloud live view; a printer that is not cloud-bound
   has no camera in this app.
4. Open the printer's **Device** page and the **camera** once, to confirm the
   live view works. This also proves the cloud session is healthy.

No camera setting on the printer is needed. The printer must be reachable for
status and pause/resume, and it must be bound to your Bambu account for the
camera.

Verify the files exist:

```bash
ls "$HOME/Library/Application Support/BambuStudio/plugins/libbambu_networking.dylib"
ls "$HOME/Library/Application Support/BambuStudio/plugins/libBambuSource.dylib"
ls "/Applications/BambuStudio.app/Contents/Resources/cert/slicer_base64.cer"
```

If any is missing, update Bambu Studio and open the printer once more.

## 4. Clone the project

```bash
mkdir -p ~/Projects
cd ~/Projects
git clone https://github.com/ismkdc/bambu-spaghetti-watch.git
cd bambu-spaghetti-watch
```

## 5. Create the virtual environment

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[dev]'
```

Check:

```bash
.venv/bin/bambu-watch --help
```

You should see `--probe`, `--once`, `--settings` and `--config`.

## 6. Build the native bridge

```bash
./bridge/build.sh
```

The script uses `.venv/bin/python` when it exists, so run it after step 5. It
builds:

- `bridge/libbswbridge.dylib` — the C ABI used to mint the camera URL
- `vendor/bambu-headless/h2d_network_bridge` — signed pause/resume
- `vendor/bambu-headless/h2d_host_identity.dylib` — the executable-path shim

If it fails with `nlohmann/json.hpp not found`, run `brew install
nlohmann-json` and try again.

## 7. Download the model

The weights are ~200 MB and are not in the repository.

```bash
curl -L -o src/bambu_spaghetti_watch/models/model-weights.darknet \
  https://www.obico.io/static_pub/ml-models/model-weights-ef79dacfd0051ab526f3002d5f5f9912.darknet
```

`model.cfg` is already included. Check:

```bash
ls -lh src/bambu_spaghetti_watch/models/
```

You should see `model-weights.darknet` (~190–210 MB) and `model.cfg`.

## 8. Probe the three layers

```bash
.venv/bin/bambu-watch --probe
```

Expected output (values will differ):

```
printer   : 192.168.1.50 (01S00A123456789)
access    : d9******
camera    : cloud

mqtt      : connecting to 192.168.1.50:8883 ...
mqtt      : ok, state=RUNNING machine=N7
camera    : 174014 bytes in 2633 ms
model     : 0 detections in 203 ms
```

What each line means:

| Line | Meaning | If it fails |
|---|---|---|
| `printer` | SSDP found the printer and the serial matched Studio's config | check the printer is on and on the same network |
| `access` | the access code came from `BambuStudio.conf` | see Troubleshooting |
| `mqtt` | printer `:8883` accepted `bblp` + access code | wrong code, or the printer is not reachable locally |
| `camera` | the cloud URL was minted and `libBambuSource` returned a JPEG | first run takes ~20 s; see Troubleshooting |
| `model` | the detector ran | missing weights, or OpenCV < 5 |

The first camera frame is slow because the cloud URL is minted on demand. It is
cached for 30 minutes in `~/Library/Caches/bambu-watch/liveview_url.txt`.

## 9. Run the menu bar app

```bash
.venv/bin/bambu-watch
```

A small icon appears in the menu bar:

| Icon | State |
|---|---|
| ○ | idle |
| ● | watching (printer `RUNNING`) |
| ▲ | warning |
| ✕ | error |

The menu shows the three-layer health, the failure score, Pause/Resume, and
**Show last frame**.

Start with `failure_action: "notify"` (the default). Watch a few clean prints,
then decide whether you want `pause`.

## 10. Enable pause (optional, and only after watching a few prints)

Create `~/.config/bambu-spaghetti-watch/config.json`:

```json
{
  "failure_action": "pause",
  "sensitivity": "medium"
}
```

Pause goes through Studio's signed control path. If the printer is still in the
default verification mode this is the only route that works; unsigned MQTT is
rejected with `84033543 mqtt message verify failed`.

## 11. Autostart at login (optional)

```bash
mkdir -p ~/Library/Logs/bambu-spaghetti-watch
cp launchd/io.github.bambu-spaghetti-watch.plist ~/Library/LaunchAgents/
```

Edit the three absolute paths inside the plist (Python, the project, the log),
then:

```bash
launchctl load ~/Library/LaunchAgents/io.github.bambu-spaghetti-watch.plist
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cannot read ~/Library/Application Support/BambuStudio/BambuStudio.conf` | open Bambu Studio, sign in, add the printer |
| `no status received` | wrong access code, or the printer is not reachable locally; check the code on the printer screen |
| `discovery failed: several printers answered SSDP` | set `serial` in the config |
| `camera error: ... bridge ...` | run `./bridge/build.sh` |
| `cloud camera could not mint a live-view URL` | open Bambu Studio once, confirm the printer is online **and bound to your Bambu account** (not local-only), then re-run `--probe` |
| `cloud camera returned no frame` | delete `~/Library/Caches/bambu-watch/liveview_url.txt` and retry |
| `Address already in use` on `:8883` | another MQTT client is connected with the same account; close it |
| Frequent false positives | lower `sensitivity` to `low` |

If `--probe` still fails, run it again with the log visible:

```bash
.venv/bin/bambu-watch --probe 2>&1 | tee /tmp/bambu-probe.log
```

The three lines that matter are `mqtt`, `camera` and `model`; include them when
asking for help.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/io.github.bambu-spaghetti-watch.plist
rm -rf ~/Projects/bambu-spaghetti-watch
rm -rf ~/Library/Caches/bambu-watch
```
