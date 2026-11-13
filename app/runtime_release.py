"""Runtime version comes from the same verified package manifest as deployment."""
import json
from pathlib import Path
def version():
 return json.loads((Path(__file__).parent.parent/"RELEASE.json").read_text(encoding="utf-8"))["version"]
