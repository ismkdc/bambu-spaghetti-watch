# Design

## Intent

A small macOS menu bar app that watches one Bambu Lab printer, runs Obico's
current failure model locally, and can pause the print when the failure signal
is sustained.

One process, one language (Python 3.12), no extra server.

## Layers

```
              ┌──────────────────────────────┐
   MQTT       │  printer.py                  │  ssl://<ip>:8883
  (8883) ◄────┤  connect, subscribe, publish │  device/<sn>/report|request
              └──────────┬───────────────────┘
                         │ state: RUNNING / idle
              ┌──────────▼───────────────────┐
   cloud      │  camera/cloud.py             │  mint URL + libBambuSource
 live view ◄──┤  one JPEG / N seconds        │  ffmpeg decodes H.264
              └──────────┬───────────────────┘
                         │ 1080p BGR
              ┌──────────▼───────────────────┐
              │  detector.py                 │  cv2.dnn + Obico darknet
              │  letterbox → forward → NMS   │  ~27 ms
              └──────────┬───────────────────┘
                         │ [(score, box)]
              ┌──────────▼───────────────────┐
              │  decision.py                 │  EWM + rolling baseline
              └──────────┬───────────────────┘
                         │ verdict
              ┌──────────▼───────────────────┐
              │  ui.py (rumps)               │  icon, menu, notification
              └──────────────────────────────┘
```

## Camera: cloud live view

The printer's own local camera paths are model-specific (`:6000` chamber image on
P1/A1, RTSPS `:322` on X1/P2S) and do not exist remotely. Bambu's cloud live
view does, and it is the same tunnel Bambu Handy uses.

The app therefore asks Studio's networking plugin for a live-view URL
(`bambu:///tutk?...`) and hands it to `libBambuSource`. The plugin signs the
request as the Studio client; the app does not implement that signing itself.

`bridge/camera_probe.py` runs the mint in a short child process so the
executable-path shim and its Python-path side effects stay out of the menu bar
app. The URL is cached for 30 minutes.

Why a shim and a one-byte hook: Studio's plugin only emits the signed request
when the process claims to be Studio, and only mints after a per-device
"encrypt flag" is set. The shim covers the first; `force_flag.js` covers the
second. The request that reaches Bambu is still the plugin's own signed
request.

## Control: signed MQTT

Firmware since 2025 verifies control commands. Raw MQTT `pause` is rejected
with `84033543 mqtt message verify failed`. The vendored `bambu-headless`
bridge performs Studio's certificate bring-up and dispatches the signed
command. `cloud_control.py` shells out to it.

## Detection

`detector.py` loads Obico's 2025-12 darknet weights through `cv2.dnn`. The
shipped cfg is patched in memory (`[reorg3d]` → `[reorg]`, `thresh = .6` →
`0.01`); the file on disk is untouched.

`decision.py` is a port of Obico's smoothing: per-frame score, EWM (span 12),
rolling short (310) and long (7200), score = `ewm − long`, warning vs failure
at 1.75× escalation, 30-frame warm-up. A single odd frame cannot fire an
action.

## Non-goals

- Cloud account management, slicing, file transfer, print queue
- Multiple printers
- A `.app` bundle, signing, notarisation, App Store
- Re-implementing Bambu's client attestation

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Studio plugin ABI changes | mint stops working | `camera_probe.py` fails loudly; `--probe` names the layer |
| Studio session expires | mint returns no URL | open Studio once; the URL is re-minted on failure |
| Model false positives | unnecessary pause | `notify` is the default; thresholds are configurable |
| Frida blocked by macOS | no mint | run `--probe`; the error names the bridge |
