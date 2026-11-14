# Install — step by step

This is the long version of the quick start. It assumes a fresh Mac and a
printer already set up in Bambu Studio and bound to your Bambu account.

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

Check:

```bash
/opt/homebrew/bin/python3.12 --version     # Python 3.12.x
ffmpeg -version | head -1
```

> On an Intel Mac, Homebrew lives in `/usr/local`, so use
> `/usr/local/bin/python3.12` everywhere below.

## 3. Prepare Bambu Studio

The app does **not** launch Bambu Studio, but it uses three things Studio
installs and one thing Studio stores:

1. `libbambu_networking.dylib` — the networking plugin (cloud session + signing)
2. `libBambuSource.dylib` — the live-view library
3. `Contents/Resources/cert/slicer_base64.cer` — the TLS certificate
4. Your signed-in session and the printer's serial

So:

1. Install Bambu Studio.
2. Sign in to your Bambu account.
3. Add the printer **and confirm it appears in your Bambu account** (cloud
   binding). The app talks to the printer through Bambu's cloud; a printer that
   is not bound to your account has no status and no camera.
4. Open the printer's **Device** page and the **camera** once, to confirm the
   live view works. This also proves the cloud session is healthy.

The printer does **not** need to be on the same network as the Mac.

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

- `bridge/libbswbridge.dylib` — the C ABI used for status, control and the
  camera mint
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
printer   : 01S00A123456789 (cloud)
camera    : cloud

cloud     : connecting to the Bambu account ...
cloud     : ok, state=RUNNING machine=N7
camera    : 174014 bytes in 2633 ms
model     : 0 detections in 203 ms
```

What each line means:

| Line | Meaning | If it fails |
|---|---|---|
| `printer` | the serial came from your Studio config | add the printer in Studio or set `serial` |
| `cloud` | the plugin signed in and a status report arrived | open Studio once and confirm the printer is online |
| `camera` | the cloud URL was minted and `libBambuSource` returned a JPEG | first run takes ~20 s |
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
| ● | watching (a job is running) |
| ▲ | warning |
| ✕ | error |

The menu shows the three-layer health, the failure score, Pause/Resume, and
**Show last frame**.

The default `failure_action` is **`stop`**: a sustained failure stops the
print. If you want to watch a few clean prints first, set it to `notify` or
`pause`.

## 10. Change the failure action (optional)

Create `~/.config/bambu-spaghetti-watch/config.json`:

```json
{
  "failure_action": "notify",
  "sensitivity": "medium"
}
```

`stop`, `pause`, `notify` and `none` are accepted. The app signs pause/resume/
stop itself through the plugin; no Developer Mode toggle is needed.

## 10b. Local Network permission (macOS 15+)

Signed pause/resume goes over the printer's LAN address, and macOS asks for
Local Network access. A LaunchAgent started by launchd cannot show that prompt,
so it is denied silently (`No route to host`). Two ways to run it with access:

```bash
# A. from Terminal, which already has the permission
./bambu-watch.command

# B. grant the app the permission once, then keep the LaunchAgent
#    System Settings -> Privacy & Security -> Local Network
#    enable Python (or the app) if it is listed
```

If you use A at login, add `bambu-watch.command` to System Settings ->
General -> Login Items and unload the LaunchAgent so only one copy runs.

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

The agent uses `RunAtLoad` and `KeepAlive`, so it starts at login and comes back
after a crash. It connects to the cloud on its own; the icon appears before the
first status report.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cannot read ~/Library/Application Support/BambuStudio/BambuStudio.conf` | open Bambu Studio, sign in, add the printer |
| `printer error: no printer saved in Bambu Studio` | add the printer in Studio, or set `serial` in the config |
| `cloud     : no status received` | open Bambu Studio once and confirm the printer is online and bound to your account |
| `camera error: ... bridge ...` | run `./bridge/build.sh` |
| `cloud camera could not mint a live-view URL` | open Bambu Studio once and confirm the printer is online; then re-run `--probe` |
| `cloud camera returned no frame` | delete `~/Library/Caches/bambu-watch/liveview_url.txt` and retry |
| Frequent false positives | lower `sensitivity` to `low` |

If `--probe` still fails, run it again with the log visible:

```bash
.venv/bin/bambu-watch --probe 2>&1 | tee /tmp/bambu-probe.log
```

The three lines that matter are `cloud`, `camera` and `model`; include them when
asking for help.

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/io.github.bambu-spaghetti-watch.plist
rm -rf ~/Projects/bambu-spaghetti-watch
rm -rf ~/Library/Caches/bambu-watch
```
