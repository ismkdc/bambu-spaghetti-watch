# Design

## Intent

A small macOS menu bar app that watches one Bambu Lab printer, runs Obico's
current failure model locally, and stops the print when the failure signal is
sustained.

One process, one language (Python 3.12), no extra server.

## Layers

```
              ┌──────────────────────────────┐
   Bambu      │  cloud_printer.py            │  plugin: cloud session
   cloud ◄────┤  push_status, signed control │  status + pause/resume/stop
              └──────────┬───────────────────┘
                         │ state: RUNNING / FINISH / …
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

## Cloud session: status and control

Bambu's networking plugin is the user's own client. The app loads it in-process
through `bridge/libbswbridge.dylib` and runs the same bring-up Studio does:
create agent, callbacks, certificate file, the `X-BBL-*` header map, `start`,
`connect_server`, device table, selected machine.

The cloud's incremental `push_status` omits `gcode_state`, so the app sends a
`pushall` after the certificate exchange and reads the full report. Between full
reports, progress fields (`mc_percent`, `layer_num`, `mc_remaining_time`) mean
"working"; a long gap means idle again.

Control commands are sent through `bambu_network_send_message_to_printer`, which
the plugin signs with the same device certificate it uses for Studio. This is
why the app does not need Developer Mode, and why it must run under the
executable-path shim: the plugin only produces the signed request when
`_NSGetExecutablePath()` looks like the Studio binary.

The shim is AGPL-3.0 code from `danielyaa5/bambu-headless`; it is interposed
inside the app process.

## Camera: cloud live view

The printer's own local camera paths are model-specific and do not exist
remotely. Bambu's cloud live view does, and it is the same tunnel Bambu Handy
uses.

`bridge/camera_probe.py` asks the plugin for a live-view URL
(`bambu:///tutk?...`) and hands it to `libBambuSource`. The mint runs in a short
child process so the shim and its Python-path side effects stay out of the menu
bar app. The URL is cached for 30 minutes and re-minted on failure.

Why a shim and a one-byte hook: the plugin only emits the signed request when
the process claims to be Studio, and only mints after a per-device "encrypt
flag" is set. The shim covers the first; `force_flag.js` covers the second. The
request that reaches Bambu is still the plugin's own signed request.

## Detection

`detector.py` loads Obico's 2025-12 darknet weights through `cv2.dnn`. The
shipped cfg is patched in memory (`[reorg3d]` → `[reorg]`, `thresh = .6` →
`0.01`); the file on disk is untouched.

`decision.py` is a port of Obico's smoothing: per-frame score, EWM (span 12),
rolling short (310) and long (7200), score = `ewm − long`, warning vs failure
at 1.75× escalation, 30-frame warm-up. A single odd frame cannot fire an
action.

## Failure action

The default is `stop`. The action layer sends the cloud-signed `stop` through
the same plugin path, so no Developer Mode is involved. `pause`, `notify` and
`none` are config overrides.

## Non-goals

- Cloud account management, slicing, file transfer, print queue
- Multiple printers
- A `.app` bundle, signing, notarisation, App Store
- Re-implementing Bambu's client attestation

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Studio plugin ABI changes | status/control/mint stop working | `--probe` names the failing layer; the plugin version is logged |
| Studio session expires | no status and no mint | open Studio once; `KeepAlive` retries |
| Cloud control rejects a command | no stop | the app logs the plugin's error code; the printer screen still shows the job |
| Model false positives | unnecessary stop | the decision layer requires a sustained score; set `failure_action: notify` to observe first |
