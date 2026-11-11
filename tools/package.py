"""Re-embed app/ + web/ + openapi.json into the inline compose recipe, zlib-compressed. Run after every source edit
(after `wam package-inline`, which regenerates the template recipe). Idempotent:
- compresses the blob (Linux limits one exec argument to 128 KiB; the plain blob is bigger),
- adds FEEDBACK_RELAY_URL to the environment (package-inline drops custom env)."""
import base64
import json
import re
import sys
import zlib
from pathlib import Path

root = Path(__file__).resolve().parent.parent
compose = root / 'runtipi' / 'docker-compose.yml'
text = compose.read_text()
m = re.search(r"b64decode\('([^']+)'\)", text)
if not m:
    sys.exit('inline blob not found; template changed')
raw = base64.b64decode(m.group(1))
packed = raw[:1] == b'x'
old = json.loads(zlib.decompress(raw) if packed else raw)
files = {name: (root / name).read_text() for name in old}
blob = base64.b64encode(zlib.compress(json.dumps(files).encode(), 9)).decode()
text = text[:m.start(1)] + blob + text[m.end(1):]
if not packed:
    a, b = 'import base64,json,pathlib,runpy\\n', "files=json.loads(base64.b64decode('"
    assert text.count(a) == 1 and text.count(b) == 1, 'loader changed'
    text = text.replace(a, 'import base64,json,pathlib,runpy,zlib\\n').replace(b, "files=json.loads(zlib.decompress(base64.b64decode('")
    text = text.replace(blob + "'))", blob + "')))", 1)
env = '      FEEDBACK_RELAY_URL: ${FEEDBACK_RELAY_URL:-}\n'
if env not in text:
    anchor = '      WIZARD_TRUSTED_ORIGINS: ${WIZARD_TRUSTED_ORIGINS:-}\n'
    assert anchor in text
    text = text.replace(anchor, anchor + env, 1)
compose.write_text(text)
arg = re.search(r'    - "(import base64[\s\S]*?)"\n    x-runtipi', text)
size = len(arg.group(1)) if arg else -1
assert 0 < size < 120 * 1024, f'inline command is {size} bytes'
print('embedded', len(files), 'files, command', size, 'bytes ->', compose)
