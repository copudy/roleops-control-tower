"""Trusted-base CI scanner. Never import or execute PR code."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import base64

HERE=Path(__file__).resolve().parent
ARCHIVE_SHA="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"

def main():
    base=os.environ["SAFETY_BASE"];head=os.environ["SAFETY_HEAD"]
    if not all(re.fullmatch("[0-9a-f]{40}",x) for x in (base,head)):raise RuntimeError("Invalid event SHA")
    with tempfile.TemporaryDirectory(prefix="safety-ci-") as tmp:
        root=Path(tmp);archive=root/"gitleaks.tar.gz"
        url="https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz"
        with urllib.request.urlopen(url,timeout=60) as source:archive.write_bytes(source.read(64*1024*1024))
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=ARCHIVE_SHA:raise RuntimeError("Scanner archive SHA mismatch")
        with tarfile.open(archive,"r:gz") as package:
            member=next((m for m in package if m.name=="gitleaks" and m.isfile()),None)
            if member is None:raise RuntimeError("Scanner binary missing")
            scanner=root/"gitleaks";scanner.write_bytes(package.extractfile(member).read());scanner.chmod(0o700)
        config={"scanner":str(scanner),"scanner_sha256":hashlib.sha256(scanner.read_bytes()).hexdigest()}
        (HERE/"installed.json").write_text(json.dumps(config),encoding="utf-8")
        token=os.environ.pop("GH_TOKEN")
        auth=base64.b64encode(("x-access-token:"+token).encode()).decode()
        # Fetch objects only; keep the checked-out, trusted base scanner intact.
        r=subprocess.run(["git","-c","http.https://github.com/.extraheader=AUTHORIZATION: basic "+auth,
                          "fetch","--no-tags","origin",head],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        token=auth=None
        if r.returncode:raise RuntimeError("Cannot fetch PR objects")
        subprocess.run([sys.executable,str(HERE/"guard.py"),"range","--base",base,"--head",head],check=True)

if __name__=="__main__":main()
