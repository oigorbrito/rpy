#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: verify_image_runtime.sh <image-ref>" >&2
  exit 2
fi

image=$1

# Exercise the exact runtime artifact, not the checkout. This catches missing
# packaged/static files and accidental root execution before publication.
docker run --rm --entrypoint python "$image" - <<'PY'
import os
from pathlib import Path

import app.api
import app.worker
import app.scheduler

frontend = Path(app.api.__file__).with_name("frontend")
required = [frontend / "index.html", frontend / "app.css", frontend / "app.js"]
missing = [str(path) for path in required if not path.is_file()]
if missing:
    raise SystemExit(f"frontend assets missing from image: {', '.join(missing)}")

if os.getuid() == 0:
    raise SystemExit("image runtime user must not be root")
PY

echo "image runtime contract: ok"
