"""Convert legacy Office bytes locally; no Canvas, database, or AI calls."""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

MAX_LEGACY_BYTES = 50 * 1024 * 1024
CONVERSION_TIMEOUT = 60
OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
OUTPUT_FORMATS = {
    "doc": ("docx", "Office Open XML Text"),
    "ppt": ("pptx", "Impress MS PowerPoint 2007 XML"),
}


class LegacyConversionError(Exception):
    """Report a safe failure category without logging document contents or paths."""

    def __init__(self, error_type, status="error"):
        super().__init__(error_type)
        self.error_type = error_type
        self.status = status


def libreoffice_binary():
    """Find a configured converter, a PATH installation, or the usual macOS app."""
    configured = os.getenv("LIBREOFFICE_PATH")
    candidates = [configured] if configured else [
        "soffice", "libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _converter_environment():
    # The converter needs OS settings, never Canvas tokens or future AI keys.
    names = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "WINDIR")
    return {name: os.environ[name] for name in names if name in os.environ}


def word_bridge_binary():
    """macOS's Word-to-HTML import preserves a trailing table LibreOffice can lose."""
    return "/usr/bin/textutil" if sys.platform == "darwin" and Path("/usr/bin/textutil").is_file() else None


@lru_cache(maxsize=8)
def _version(binary, modified_at, size):
    try:
        result = subprocess.run(
            [binary, "--version"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, check=False, env=_converter_environment(),
        )
        if result.returncode == 0:
            version = result.stdout.decode("utf-8", errors="replace").strip()[:160]
            if version:
                return version
    except (OSError, subprocess.TimeoutExpired):
        pass
    return f"LibreOffice-build-{modified_at}-{size}"


def converter_fingerprint(format_name=None):
    """Include converter availability/version in cache keys; no conversion occurs."""
    binary = libreoffice_binary()
    if not binary:
        return "LibreOffice-not-installed"
    try:
        details = Path(binary).stat()
        version = _version(binary, details.st_mtime_ns, details.st_size)
        bridge = word_bridge_binary() if format_name == "doc" else None
        if bridge:
            native = Path(bridge).stat()
            version += f":textutil-{native.st_mtime_ns}-{native.st_size}"
        return version
    except OSError:
        return "LibreOffice-unavailable"


def _prepare_profile(profile):
    """Use a disposable profile with macros/active content and link updates disabled."""
    user = profile / "user"
    user.mkdir(parents=True)
    (user / "registrymodifications.xcu").write_text('''<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Common/Security/Scripting">
    <prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>
    <prop oor:name="DisableActiveContent" oor:op="fuse"><value>true</value></prop>
    <prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value></prop>
  </item>
  <item oor:path="/org.openoffice.Office.Writer/Content/Update">
    <prop oor:name="Link" oor:op="fuse"><value>2</value></prop>
    <prop oor:name="Field" oor:op="fuse"><value>false</value></prop>
    <prop oor:name="Chart" oor:op="fuse"><value>false</value></prop>
  </item>
</oor:items>''', encoding="utf-8")


def _run_conversion(command, directory):
    process = subprocess.Popen(
        command, cwd=directory, env=_converter_environment(),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=(os.name == "posix"),
    )
    try:
        return process.wait(timeout=CONVERSION_TIMEOUT)
    except BaseException as error:
        # Kill this conversion's process group, including any launcher child.
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait()
        if isinstance(error, subprocess.TimeoutExpired):
            raise LegacyConversionError("LegacyConversionTimeout") from None
        raise


def convert_legacy_office(content, format_name):
    """Return modern Office bytes; remove source, output, and profile on every exit."""
    if format_name not in OUTPUT_FORMATS:
        raise ValueError("Only doc and ppt are accepted by the legacy converter.")
    if len(content) > MAX_LEGACY_BYTES:
        raise LegacyConversionError("LegacyFileLimit", "too_large")
    # Word can also store RTF with a .doc extension. Arbitrary bytes are not a
    # valid document merely because a converter could import them as plain text.
    if not content.startswith(OLE_SIGNATURE) and not (format_name == "doc" and content.lstrip().startswith(b"{\\rtf")):
        raise LegacyConversionError("InvalidLegacyOffice")
    binary = libreoffice_binary()
    if not binary:
        raise LegacyConversionError("LibreOfficeNotInstalled", "unavailable")
    target, filter_name = OUTPUT_FORMATS[format_name]
    try:
        with tempfile.TemporaryDirectory(prefix="easycanvas-convert-") as directory:
            root = Path(directory).resolve()
            source = root / f"input.{format_name}"
            source.write_bytes(content)
            bridge = word_bridge_binary() if format_name == "doc" else None
            if bridge:
                html = root / "input.html"
                code = _run_conversion([
                    bridge, "-noload", "-nostore", "-convert", "html",
                    "-output", str(html), str(source),
                ], root)
                if code != 0 or not html.is_file():
                    raise LegacyConversionError("LegacyWordPreparationFailed")
                if html.stat().st_size > MAX_LEGACY_BYTES:
                    raise LegacyConversionError("ConvertedFileLimit", "too_large")
                # Use the native bridge for table-bearing documents. Other Word
                # files keep LibreOffice's direct import, including headers.
                if b"<table" in html.read_bytes():
                    source = html
            profile = root / "profile"
            _prepare_profile(profile)
            output = root / "output"
            output.mkdir()
            command = [
                binary, f"-env:UserInstallation={profile.as_uri()}",
                "--headless", "--nologo", "--nodefault", "--norestore",
                "--convert-to", f"{target}:{filter_name}", "--outdir", str(output), str(source),
            ]
            code = _run_conversion(command, root)
            converted = output / f"input.{target}"
            if code != 0 or not converted.is_file() or converted.resolve().parent != output:
                raise LegacyConversionError("LegacyConversionFailed")
            size = converted.stat().st_size
            if size > MAX_LEGACY_BYTES:
                raise LegacyConversionError("ConvertedFileLimit", "too_large")
            if size == 0:
                raise LegacyConversionError("EmptyConvertedFile")
            return converted.read_bytes()
    except OSError:
        raise LegacyConversionError("LegacyConverterUnavailable", "unavailable") from None
