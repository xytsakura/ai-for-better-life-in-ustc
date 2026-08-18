from __future__ import annotations

import base64
import os
import stat
from pathlib import Path


KEY_PATH = Path(os.getenv("HUB_MODEL_PROFILE_MASTER_KEY_FILE", "/run/hub-model-secrets/master.key"))


def main() -> None:
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if KEY_PATH.exists() and KEY_PATH.read_text(encoding="utf-8").strip():
        print("Hub model profile master key already exists.")
        return

    encoded = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
    temporary = KEY_PATH.with_suffix(".tmp")
    temporary.write_text(encoded + "\n", encoding="utf-8")
    try:
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    temporary.replace(KEY_PATH)
    print("Created runtime-only Hub model profile master key.")


if __name__ == "__main__":
    main()
