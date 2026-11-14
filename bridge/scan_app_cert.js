// Read-only: find the app certificate and private key the plugin decrypted.
// The embedded material is AES-GCM encrypted in the dylib; the plugin decrypts
// it at startup, so the plaintext lives in this process's heap.

function hexp(s) {
  return s.split('').map(c => c.charCodeAt(0).toString(16).padStart(2, '0')).join(' ');
}
function ascii(buf) {
  let out = '';
  for (const b of new Uint8Array(buf)) out += (b >= 32 && b < 127) ? String.fromCharCode(b) : '.';
  return out;
}
function readBlock(addr, max = 6000) {
  try { return addr.readCString(max); } catch (e) { return null; }
}

const MARKERS = [
  '-----BEGIN CERTIFICATE-----',
  '-----BEGIN PRIVATE KEY-----',
  '-----BEGIN RSA PRIVATE KEY-----',
  '-----BEGIN EC PRIVATE KEY-----',
];
const out = [];
for (const marker of MARKERS) {
  for (const r of Process.enumerateRanges('rw-')) {
    if (r.size > 64 * 1024 * 1024) continue;
    let res = [];
    try { res = Memory.scanSync(r.base, r.size, hexp(marker)); } catch (e) { continue; }
    for (const m of res) {
      const text = readBlock(m.address);
      out.push({ marker, at: m.address.toString(), text });
    }
  }
}
send({ event: 'certs', count: out.length, out });
