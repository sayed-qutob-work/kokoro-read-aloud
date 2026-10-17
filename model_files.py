"""The Kokoro model files, pinned: which files, from which revision, and
their SHA-256.

    python3 model_files.py fetch     download what is missing, verified
    python3 model_files.py verify    check every file against its SHA-256
    python3 model_files.py where     print the folder

Once all of them are in place the server loads the model and voices by
path and never touches the network (tts_server.KokoroEngine). Without them
kokoro downloads into the Hugging Face cache, as it always has, and checks
huggingface.co for updates on every start (AUDIT 2026-10-17).

A download is written to <name>.part and only takes its real name once its
size and SHA-256 match, so a file under its real name is a verified one.

Stdlib only: setup runs this on the system Python, before the venv exists.
"""

import hashlib
import os
import sys
import urllib.request

sys.dont_write_bytecode = True     # no __pycache__ next to the code, see paths.py
import paths                       # noqa: E402

REPO = "hexgrad/Kokoro-82M"
REVISION = "f3ff3571791e39611d31c381e3a41a3af07b4987"
DIR = os.path.join(paths.DATA, "models", "Kokoro-82M")

# (path in the repo, bytes, SHA-256), from the Hugging Face API at REVISION
# and checked against the files the server had downloaded itself. The voices
# are the 28 English ones (af_, am_, bf_, bm_): the server reads English only.
FILES = [
    ("config.json", 2351, "5abb01e2403b072bf03d04fde160443e209d7a0dad49a423be15196b9b43c17f"),
    ("kokoro-v1_0.pth", 327212226, "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4"),
    ("voices/af_alloy.pt", 523425, "6d877149dd8b348fbad12e5845b7e43d975390e9f3b68a811d1d86168bef5aa3"),
    ("voices/af_aoede.pt", 523425, "c03bd1a4c3716c2d8eaa3d50022f62d5c31cfbd6e15933a00b17fefe13841cc4"),
    ("voices/af_bella.pt", 523425, "8cb64e02fcc8de0327a8e13817e49c76c945ecf0052ceac97d3081480e8e48d6"),
    ("voices/af_heart.pt", 523425, "0ab5709b8ffab19bfd849cd11d98f75b60af7733253ad0d67b12382a102cb4ff"),
    ("voices/af_jessica.pt", 523435, "cdfdccb8cc975aa34ee6b89642963b0064237675de0e41a30ae64cc958dd4e87"),
    ("voices/af_kore.pt", 523420, "8bfbc512321c3db49dff984ac675fa5ac7eaed5a96cc31104d3a9080e179d69d"),
    ("voices/af_nicole.pt", 523430, "c5561808bcf5250fe8c5f5de32caf2d94f27e57e95befdb098c5c85991d4c5da"),
    ("voices/af_nova.pt", 523420, "e0233676ddc21908c37a1f102f6b88a59e4e5c1bd764983616eb9eda629dbcd2"),
    ("voices/af_river.pt", 523425, "e149459bd9c084416b74756b9bd3418256a8b839088abb07d463730c369dab8f"),
    ("voices/af_sarah.pt", 523425, "49bd364ea3be9eb3e9685e8f9a15448c4883112a7c0ff7ab139fa4088b08cef9"),
    ("voices/af_sky.pt", 523351, "c799548aed06e0cb0d655a85a01b48e7f10484d71663f9a3045a5b9362e8512c"),
    ("voices/am_adam.pt", 523420, "ced7e284aba12472891be1da3ab34db84cc05cc02b5889535796dbf2d8b0cb34"),
    ("voices/am_echo.pt", 523420, "8bcfdc852bc985fb45c396c561e571ffb9183930071f962f1b50df5c97b161e8"),
    ("voices/am_eric.pt", 523420, "ada66f0eefff34ec921b1d7474d7ac8bec00cd863c170f1c534916e9b8212aae"),
    ("voices/am_fenrir.pt", 523430, "98e507eca1db08230ae3b6232d59c10aec9630022d19accac4f5d12fcec3c37a"),
    ("voices/am_liam.pt", 523420, "c82550757ddb31308b97f30040dda8c2d609a9e2de6135848d0a948368138518"),
    ("voices/am_michael.pt", 523435, "9a443b79a4b22489a5b0ab7c651a0bcd1a30bef675c28333f06971abbd47bd37"),
    ("voices/am_onyx.pt", 523420, "e8452be16cd0f6da7b4579eaf7b1e4506e92524882053d86d72b96b9a7fed584"),
    ("voices/am_puck.pt", 523420, "dd1d8973f4ce4b7d8ae407c77a435f485dabc052081b80ea75c4f30b84f36223"),
    ("voices/am_santa.pt", 523425, "7f2f7582fa2b1f160e90aafe6d0b442a685e773608b6667e545d743b073e97a7"),
    ("voices/bf_alice.pt", 523425, "d292651b6af6c0d81705c2580dcb4463fccc0ff7b8d618a471dbb4e45655b3f3"),
    ("voices/bf_emma.pt", 523420, "d0a423deabf4a52b4f49318c51742c54e21bb89bbbe9a12141e7758ddb5da701"),
    ("voices/bf_isabella.pt", 523440, "cdd4c37003805104d1d08fb1e05855c8fb2c68de24ca6e71f264a30aaa59eefd"),
    ("voices/bf_lily.pt", 523420, "6e09c2e481e2d53004d7e5ae7d3a325369e130a6f45c35a6002de75084be9285"),
    ("voices/bm_daniel.pt", 523430, "fc3fce4e9c12ed4dbc8fa9680cfe51ee190a96444ce7c3ad647549a30823fc5d"),
    ("voices/bm_fable.pt", 523425, "d44935f3135257a9064df99f007fc1342ff1aa767552b4a4fa4c3b2e6e59079c"),
    ("voices/bm_george.pt", 523430, "f1bc812213dc59774769e5c80004b13eeb79bd78130b11b2d7f934542dab811b"),
    ("voices/bm_lewis.pt", 523425, "b5204750dcba01029d2ac9cec17aec3b20a6d64073c579d694a23cb40effbd0e"),
]


def path(name):
    return os.path.join(DIR, *name.split("/"))


def complete():
    """Every file present at its pinned size. No hashing, so it is cheap
    enough for each server start; a file only ever gets its real name after
    its SHA-256 matched (fetch)."""
    return all(os.path.isfile(path(n)) and os.path.getsize(path(n)) == size
               for n, size, _ in FILES)


def voice(name):
    """Path of a voice's file, or None when it is not one of these."""
    p = path(f"voices/{name}.pt")
    return p if os.path.isfile(p) else None


def _matches(name, size, sha):
    p = path(name)
    if not os.path.isfile(p) or os.path.getsize(p) != size:
        return False
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest() == sha


def verify():
    """Names of the files that are missing or do not match their SHA-256."""
    return [n for n, size, sha in FILES if not _matches(n, size, sha)]


def fetch(progress=None):
    """Download every file that is missing or wrong. progress(done, total,
    name) is called as bytes arrive. Raises on a file that does not match
    its SHA-256, leaving nothing under its real name."""
    todo = [(n, size, sha) for n, size, sha in FILES
            if not _matches(n, size, sha)]
    total, done = sum(size for _, size, _ in todo), 0
    for name, size, sha in todo:
        dest = path(name)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        part = dest + ".part"
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        req = urllib.request.Request(url, headers={"User-Agent": "kokoro-read-aloud"})
        h = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total, name)
        if os.path.getsize(part) != size or h.hexdigest() != sha:
            os.remove(part)
            raise ValueError(f"{name}: the download does not match its pinned "
                             f"SHA-256 ({h.hexdigest()})")
        os.replace(part, dest)
    return len(todo)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "verify"
    if cmd == "where":
        print(DIR)
    elif cmd == "verify":
        bad = verify()
        print("ok" if not bad else "missing or wrong: " + ", ".join(bad))
        return 1 if bad else 0
    elif cmd == "fetch":
        last = [-1]

        def show(done, total, name):
            pct = done * 100 // total
            if pct != last[0]:
                last[0] = pct
                print(f"\r{pct:3d}%  {done / 2**20:6.1f} / {total / 2**20:.1f} MiB  {name:28}",
                      end="", flush=True)
        n = fetch(show)
        print(("\n" if n else "") + f"ok: {n} downloaded, {len(FILES) - n} already in place")
    else:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
