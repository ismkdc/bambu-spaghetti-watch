// Diagnostic only, inside the probe process: make the plugin believe the
// device has an encrypt flag so get_camera_url proceeds to /ttcode.
// Hook 0x276f1c returns the lookup result and writes the flag byte at args[2].

const MODULE = 'libbambu_networking.dylib';

function install(mod) {
  Interceptor.attach(mod.base.add(0x276f1c), {
    onEnter(args) {
      this.flag = args[2];
    },
    onLeave(retval) {
      try { if (this.flag && !this.flag.isNull()) this.flag.writeU8(1); } catch (e) {}
      retval.replace(1);
    },
  });
  send({ event: 'hooked', base: mod.base.toString() });
}

let done = false;
const timer = setInterval(function () {
  if (done) return;
  const mod = Process.findModuleByName(MODULE);
  if (mod) { done = true; clearInterval(timer); install(mod); }
}, 100);
