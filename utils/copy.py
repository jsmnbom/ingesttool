from pathlib import Path
import typing as t
import os
import shutil
from sh import touch

BUFFER_SIZE = 4096 * 1024


class SameFileError(OSError):
    """Raised when source and destination are the same file."""


class SpecialFileError(OSError):
    """Raised when trying to do a kind of operation (e.g. copying) which is
    not supported on a special file (e.g. a named pipe)"""


def copy_with_callback(
    src: t.Union[Path, str],
    dest: t.Union[Path, str],
    callback:t.Callable[[int, int, int], None] = None,
    follow_symlinks=True,
    buffer_size=BUFFER_SIZE
):
    srcfile = Path(src)
    destpath = Path(dest)

    if not srcfile.is_file():
        raise FileNotFoundError(f"src file `{src}` doesn't exist")

    destfile = destpath / srcfile.name if destpath.is_dir() else destpath

    if destfile.exists() and srcfile.samefile(destfile):
        raise SameFileError(
            f"source file `{src}` and destinaton file `{dest}` are the same file."
        )

    # check for special files, lifted from shutil.copy source
    for fname in [srcfile, destfile]:
        try:
            st = os.stat(str(fname))
        except OSError:
            # File most likely does not exist
            pass
        else:
            if shutil.stat.S_ISFIFO(st.st_mode):
                raise SpecialFileError(f"`{fname}` is a named pipe")

    if callback is not None and not callable(callback):
        raise ValueError("callback is not callable")
    
    stat = srcfile.stat()

    if not follow_symlinks and srcfile.is_symlink():
        if destfile.exists():
            os.unlink(destfile)
        os.symlink(os.readlink(str(srcfile)), str(destfile))
    else:
        size = os.stat(src).st_size
        tries = 0
        while tries < 3:
            tries += 1
            try:
                with open(srcfile, "rb") as fsrc:
                    with open(destfile, "wb") as fdest:
                        _copyfileobj(
                            fsrc, fdest, callback=callback, total=size, length=buffer_size
                        )
                break
            except PermissionError:
                if tries == 3:
                    raise
                else:
                    continue

    shutil.copymode(str(srcfile), str(destfile))
    touch("-d", f'@{stat.st_ctime}', str(destfile))
    return str(destfile)


def _copyfileobj(fsrc, fdest, callback, total, length):
    """ copy from fsrc to fdest
    Args:
        fsrc: filehandle to source file
        fdest: filehandle to destination file
        callback: callable callback that will be called after every length bytes copied
        total: total bytes in source file (will be passed to callback)
        length: how many bytes to copy at once (between calls to callback)
    """
    copied = 0
    while True:
        buf = fsrc.read(length)
        if not buf:
            break
        fdest.write(buf)
        copied += len(buf)
        if callback is not None:
            callback(len(buf), copied, total)