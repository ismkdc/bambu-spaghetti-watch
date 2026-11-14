# Native bridge

Two jobs, both using libraries already installed by Bambu Studio:

1. **Cloud camera** — mint the live-view URL and read frames.
2. **Signed control** — pause/resume/stop through the same signed path Studio
   uses.

Nothing from Bambu is bundled or re-signed. The plugin stays in the user's
Studio installation, and the account stays in the user's Studio session.

## Build

```bash
./bridge/build.sh
```

Requires Xcode Command Line Tools and `nlohmann-json`.

## Cloud camera

```
camera_probe.py  ->  libbswbridge.dylib  ->  libbambu_networking.dylib
        |                                            |
        |  self-attach frida + force_flag.js         |  signs /ttcode
        v                                            v
   create agent, header map, start, connect_server, select machine
        |
        +--> live-view URL (bambu:///tutk?...)
                 |
                 v
        libBambuSource.dylib  ->  H.264  ->  ffmpeg  ->  one JPEG
```

`camera_probe.py` does three things that matter:

1. **Runs under the executable-path shim.** Studio's plugin only produces the
   signed request when `_NSGetExecutablePath()` looks like the Studio binary.
   `vendor/bambu-headless/h2d_host_identity.dylib` interposes it inside this
   process; `H2D_STUDIO_EXECUTABLE` points at the installed Studio binary.
2. **Sends Studio's real header map.** The plugin needs the `X-BBL-Client-*`,
   `X-BBL-Device-ID`, `X-BBL-Language`, `X-BBL-OS-*` and `accept` headers that
   Studio installs at cold boot. Missing values make `/ttcode` answer 403.
3. **Passes the encrypt-flag gate.** The plugin refuses to mint until the
   per-device "encrypt flag" is set, which normally follows a certificate
   exchange with the printer. `force_flag.js` is a one-byte, in-process hook
   that makes the lookup pass. The `/ttcode` request itself is still the
   plugin's own signed request; there is no server-side bypass.

The URL is cached in `~/Library/Caches/bambu-watch/liveview_url.txt` for 30
minutes and re-minted on failure.

`libBambuSource` may report `Mode=LAN` in its own log when the printer happens
to be on the same network. That is the library's transport choice for a
TUTK ticket; the URL is still the cloud one and works remotely.

## Signed control

`src/bambu_spaghetti_watch/cloud_control.py` calls the vendored
[`bambu-headless`](https://github.com/danielyaa5/bambu-headless) CLI, which
loads the same plugin, performs the certificate bring-up and dispatches a signed
`pause`/`resume`/`stop`.

Firmware since 2025 rejects unsigned control commands with
`84033543 mqtt message verify failed`, so this route is the only one that works
without Developer Mode.

## Files

| File | Purpose |
|---|---|
| `bsw_bridge.cpp` | C ABI over the plugin's C++ ABI (`ctypes` cannot call `std::string`/`std::function`) |
| `camera_probe.py` | headless cloud mint; writes the URL to stdout and `--out` |
| `force_flag.js` | one-byte encrypt-flag hook, loaded into the probe process |
| `build.sh` | builds this bridge and the vendored control bridge/shim |
| `eval_detector.py` | scores `tests/fixtures/print_failure/` with the shipped model |

## Privacy

The app never sends frames anywhere. Detection is local. The only network calls
are to the printer on the LAN (status/control) and to Bambu's cloud for the
live-view ticket, using the user's existing Studio session.
