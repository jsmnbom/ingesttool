import io
import struct

import piexif._load
from piexif._exceptions import InvalidImageDataError
from piexif import _webp

def read_exif_from_file(f: io.BufferedIOBase):
    """Slices JPEG meta data into a list from JPEG binary data.
    """
    f.seek(0)
    data = f.read(6)

    if data[0:2] != b"\xff\xd8":
        raise InvalidImageDataError("Given data isn't JPEG.")

    head = data[2:6]
    HEAD_LENGTH = 4
    exif = None
    while len(head) == HEAD_LENGTH:
        length = struct.unpack(">H", head[2: 4])[0]

        if head[:2] == b"\xff\xe1":
            segment_data = f.read(length - 2)
            if segment_data[:4] != b'Exif':
                head = f.read(HEAD_LENGTH)
                continue
            exif = head + segment_data
            break
        elif head[0:1] == b"\xff":
            f.read(length - 2)
            head = f.read(HEAD_LENGTH)
        else:
            break

    return exif

class CustomExifReader(piexif._load._ExifReader):
    def __init__(self, f: io.BufferedIOBase):
        f.seek(0)
        magic_number = f.read(2)
        
        if magic_number == b"\xff\xd8":  # JPEG
            app1 = read_exif_from_file(f)
            if app1:
                self.tiftag = app1[10:]
            else:
                self.tiftag = None
        elif magic_number in (b"\x49\x49", b"\x4d\x4d"):  # TIFF
            f.seek(0)
            self.tiftag = f.read()
        else:
            f.seek(0)
            header = f.read(12)
            if header[0:4] == b"RIFF"and header[8:12] == b"WEBP":
                f.seek(0)
                file_data = f.read()
                self.tiftag = _webp.get_exif(file_data)
            else:
                raise InvalidImageDataError("Given file is neither JPEG nor TIFF.")

piexif._load._ExifReader = CustomExifReader

load = piexif._load.load
TAGS = piexif.TAGS