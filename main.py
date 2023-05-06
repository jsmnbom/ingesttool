#!/usr/bin/env python3

import copy
from dataclasses import dataclass
import datetime
import io
import json
from pathlib import Path
from contextlib import nullcontext
import re
import logging
import sqlite3
import subprocess
import sys
from typing import TypedDict
import mmap

from tomlkit import parse
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from utils.path import PathMatch, longest_path_part, re_glob
from utils.template import Template
from utils.copy import copy_with_callback
from utils.bar import bytes_bar, simple_bar
from utils.utils import UnionDict
from utils import exif

CONFIG_FILE = 'ingest.toml'
DB_FILE = 'ingest.db'

class IngestBlock:
    name: str
    source: str
    destination: str
    exif: bool

@dataclass
class IngestFile:
    ingest_block: IngestBlock

    source: PathMatch
    destination: Template

    destination_path: Path = None

def load_config():
    with open('ingest.toml', 'r') as f:
        config = parse(f.read())
        return config
    
def load_db():
    db = sqlite3.connect(DB_FILE, isolation_level=None)
    db.execute('pragma journal_mode=wal;')
    cur = db.cursor()
    cur.execute('CREATE TABLE IF NOT EXISTS files (ingest_block_name TEXT NOT NULL, source TEXT NOT NULL, size INTEGER NOT NULL, mtime datetime NOT NULL, sha1 BLOB, destination TEXT, PRIMARY KEY(ingest_block_name, source, size, mtime))')
    return db

class Config(TypedDict):
    ingest: list[IngestBlock]
    var: dict[str, str]

class Tz(datetime.tzinfo):
    def __init__(self, offset: datetime.timedelta):
        self.offset = offset

    def dst(self, dt):
        return self.offset
    
    def utcoffset(self, dt):
        return self.offset

class DestinationContext(dict):
    def __init__(self, ingest_file: IngestFile, f: io.BufferedIOBase) -> None:
        
        self.ingest_file = ingest_file
        self.f = f

        self._exif = None
        self._ffprobe = None

    def get_exif(self):
        raw = exif.load(self.f)

        data = {}

        for key in raw['0th'].keys():
            data[exif.TAGS['Image'][key]['name']] = raw['0th'][key]

        for key in raw['Exif'].keys():
            data[exif.TAGS['Exif'][key]['name']] = raw['Exif'][key]
        
        # TODO: Does this apply for all cameras?
        if 'DateTime' in data:
            data['DateTime'] = datetime.datetime.strptime(data['DateTime'].decode('utf-8'), '%Y:%m:%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc)
            if 'OffsetTime' in data:
                m = re.match(r'([+-])(\d{2}):(\d{2})', data['OffsetTime'].decode('utf-8'))
                if m:
                    offset = datetime.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
                    if m.group(1) == '-':
                        offset = -offset

                    data['DateTime'] = data['DateTime'].astimezone(Tz(offset))
        
        return data
    
    def get_ffprobe(self):
        cmd = ['ffprobe', '-hide_banner', '-loglevel', 'fatal', '-show_error', '-show_format', '-show_streams', '-show_programs', '-show_chapters', '-show_private_data', '-print_format', 'json', self.ingest_file.source.path]
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        return json.loads(output.decode('utf-8'))

    def __getitem__(self, key):
        if key == 'stat':
            return self.ingest_file.stat
        elif key == 'm':
            return self.ingest_file.source.match
        elif key == 'ext':
            return self.ingest_file.source.path.suffix.lower()
        elif key == 'exif':
            if self._exif is None:
                self._exif = self.get_exif()
            return self._exif
        elif key == 'ffprobe':
            if self._ffprobe is None:
                self._ffprobe = self.get_ffprobe()
            return self._ffprobe
        else:
            return super().__getitem__(key)

class IngestTool:
    def __init__(self, config, db) -> None:
        self.config: Config = config
        self.db = db
        self.context = {'var': {k: Template(v) for k, v in self.config['var'].items()}}
        self.logger = logging.getLogger('ingest')

    def gather_files(self):
        '''Gather a list of all files that can be ingested'''
        files: dict[Path, IngestFile] = {}

        for ingest_block in self.config['ingest']:
            file_count = 0

            source = Template(ingest_block['source']).render(UnionDict(copy.deepcopy(self.context)))
            destination = Template(ingest_block['destination'])

            path, pattern = longest_path_part(source)

            try:
                for file_match in re_glob(Path(path), f'^{path}{pattern}$'):
                    files[file_match.path] = IngestFile(ingest_block, source=file_match, destination=copy.copy(destination))
                    file_count += 1
            except FileNotFoundError:
                continue
            
            if file_count == 0:
                self.logger.warning(f'No files found in ingest source: {ingest_block["name"]}')
            else:
                self.logger.info(f'Found {file_count} files in ingest source: {ingest_block["name"]}')
        return files

    def remove_existing(self, files: dict[Path, IngestFile]):
        '''Remove files that already exist in our database'''
        cur = self.db.cursor()
        for file in simple_bar(list(files.values()), desc='Checking for existing files'):
            cur.execute('SELECT * FROM files WHERE ingest_block_name = ? AND source = ? AND size = ? AND mtime = ?', (file.ingest_block.name, str(file.source.path), file.source.stat.st_size, datetime.datetime.fromtimestamp(file.source.stat.st_mtime)))
            if cur.fetchone() is not None:
                del files[file.source.path]

    def _process_file(self, file: IngestFile, bar: tqdm):
        '''Process a single file'''
        with file.source.path.open('rb') as f_source:
            with mmap.mmap(f_source.fileno(), 0, access=mmap.ACCESS_READ) as f_mem:
                self._prepare_file(file, f_mem, bar)
                self.logger.debug(f'Copying {file.source.path} -> {file.destination_path}...')
                self._copy_file(file, f_source, bar)

    def _prepare_file(self, file: IngestFile, f: mmap.mmap, bar: tqdm):
        # Get the destination path
        context = DestinationContext(file, f)
        file.destination_path = Path(file.destination.render(UnionDict(copy.deepcopy(self.context), context)))

        # Check if the file already exists in the destination
        if file.destination_path.exists():
            # TODO: Implment a way to add a counter to the destination path if it already exists (e.g. IMG_0001.jpg -> IMG_0001_1.jpg)
            bar.total -= file.source.stat.st_size
            if file.destination_path.stat().st_size == file.source.stat.st_size:
                # Skip the file if it already exists
                self.logger.debug(f'{file.source.path} -> {file.destination_path} already exists in the destination and is same size. Skipping...')
                return
            else:
                self.logger.warning(f'{file.source.path} -> {file.destination_path} already exists in the destination but is a different size. Skipping...')
                return
    
    def _copy_file(self, file: IngestFile, f: io.BufferedReader, bar: tqdm):
        # Make parent directories
        file.destination_path.parent.mkdir(parents=True, exist_ok=True)

        with file.destination_path.open('wb') as df:
            blksize = file.source.stat.st_blksize

            # Copy the file
            f.seek(0)
            while True:
                buf = f.read(blksize)
                if not buf:
                    break
                df.write(buf)
                bar.update(len(buf))

            df.close()

        # Add to database
        cur = self.db.cursor()
        cur.execute('INSERT INTO files (ingest_block_name, source, destination, size, mtime) VALUES (?, ?, ?, ?, ?)', (file.ingest_block.name, str(file.source.path), str(file.destination_path), file.source.stat.st_size, datetime.datetime.fromtimestamp(file.source.stat.st_mtime)))

    def process_files(self, files: dict[Path, IngestFile]):
        '''Process the files'''
        bar = bytes_bar(total=sum(file.source.stat.st_size for file in files.values()), desc='Processing files')
        for file in files.values():
            self._process_file(file, bar)


    def ingest(self):
        files = self.gather_files()
        self.remove_existing(files)
        self.process_files(files)

def main():
    config = load_config()
    db = load_db()

    ingest_tool = IngestTool(config, db)
    ingest_tool.ingest()

if __name__ == '__main__':
    logger_needs_redirect = sys.stdout.isatty()

    logging.basicConfig(stream=sys.stdout, level=logging.WARNING, format='%(asctime)s %(levelname)-7s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    logging.getLogger("sh").setLevel(logging.ERROR)
    logging.getLogger("ingest").setLevel(logging.DEBUG)
    with logging_redirect_tqdm() if logger_needs_redirect else nullcontext():
        main()