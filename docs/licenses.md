# Credits and third-party licenses

The code in this repository is MIT-licensed (see [LICENSE](../LICENSE)). It
builds on the work below, and redistributes none of it: uv or pip installs
the libraries from PyPI and PyTorch's index, the model downloads from Hugging
Face, and the programs come from your own system.

**Not affiliated with hexgrad or the Kokoro project.** "Kokoro" in the name
says which model this uses, not who made it.

| Component | License | Used for | How it gets to you |
|---|---|---|---|
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M), weights and voices | Apache-2.0 | The voice | Hugging Face, during setup (on Windows, on the first run) |
| [kokoro](https://github.com/hexgrad/kokoro) and [misaki](https://github.com/hexgrad/misaki), by hexgrad | Apache-2.0 | Running the model, turning text into phonemes | PyPI |
| [eSpeak NG](https://github.com/espeak-ng/espeak-ng) | GPL-3.0 | Phonemes for words misaki's dictionary doesn't know | PyPI, as a library inside `espeakng-loader`. misaki always loads that copy, never a system install |
| [phonemizer-fork](https://pypi.org/project/phonemizer-fork/) | GPL-3.0+ | misaki's interface to eSpeak NG | PyPI |
| [PyTorch](https://pytorch.org) | BSD-3-Clause | Running the model | PyTorch's own index, download.pytorch.org |
| NVIDIA CUDA libraries | NVIDIA proprietary | GPU support, CUDA installs only | PyPI, pulled in by the CUDA build of PyTorch |
| [spaCy](https://spacy.io) and `en_core_web_sm` | MIT | English text analysis inside misaki | spaCy from PyPI, `en_core_web_sm` from spaCy's GitHub releases, pinned with its hash |
| [num2words](https://github.com/savoirfairelinux/num2words) | LGPL-2.1 | Reading numbers as words | PyPI |
| Flask, NumPy, sounddevice (with PortAudio), transformers, huggingface_hub, comtypes | BSD-3-Clause, MIT, Apache-2.0 | Server, audio output, model download, Windows UI Automation | PyPI |
| [uv](https://github.com/astral-sh/uv) | MIT or Apache-2.0 | Installs Python and the packages on Linux | Your distribution, or a pinned release from GitHub that setup downloads |
| CPython 3.12, built by [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | PSF-2.0, plus the licenses of the libraries it bundles | Runs the server on Linux | GitHub, downloaded by uv during setup |
| Tcl/Tk, through Python's tkinter | TCL (BSD-style) | Settings panel and caption strip | Your Python or your distribution |
| [AutoHotkey v2](https://www.autohotkey.com/) | GPL-2.0 | Windows hotkeys | You install it |
| wl-clipboard, jq, curl, notify-send (libnotify), python-xlib | GPL-3.0+, MIT, curl, LGPL-2.1+, LGPL-2.1+ | Linux hotkey script | Your distribution |
| [Omarchy](https://omarchy.org)'s shell, built on [Quickshell](https://quickshell.org) | MIT, LGPL-3.0 | Hosts the bar widget, whose panel uses the shell's own components | Part of Omarchy |
| [input-remapper](https://github.com/sezanzeb/input-remapper), optional | GPL-3.0+ | The mouse-button trigger on Linux | Your distribution or the AUR |

Because nothing above is bundled, using, modifying or forking this repository
carries no obligation beyond the MIT license. That changes for anyone who
**redistributes a bundle** that includes these parts, such as a zip with the
virtualenv, a frozen executable or a container image:

- eSpeak NG and phonemizer-fork are GPL-3.0, so a bundle containing them must
  be distributed under the GPL-3.0's terms, with their source code available.
- The Apache-2.0, BSD and MIT components need their license texts and notices
  included.
- The NVIDIA libraries may only be redistributed under NVIDIA's own terms.
