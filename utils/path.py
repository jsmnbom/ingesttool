from collections import namedtuple
import logging
import os
import re
import typing as t
from pathlib import Path
import stat

class PathMatch:
    def __init__(self, path: Path, match: t.Match, stat: os.stat_result):
        self.path = path
        self.match = match
        self.stat = stat

    def __repr__(self):
        return f'PathMatch({self.path}, {self.match})'

def re_glob(path: Path, pattern: str) -> t.Generator[PathMatch, None, None]:
    """Recursively glob a path with a regex pattern"""
    try:
        for subpath in path.iterdir():
            s = subpath.stat()
            if stat.S_ISDIR(s.st_mode):
                yield from re_glob(subpath, pattern)
            elif stat.S_ISREG(s.st_mode):
                if match := re.match(pattern, str(subpath)):
                    yield PathMatch(subpath, match, s)
    except FileNotFoundError:
        logging.warning(f'Ingest source path not found: {path}')
        raise FileNotFoundError

# ChatGPT wrote this lmao
def longest_path_part(string_with_regex):
    # Split the input string into parts using "/"
    parts = string_with_regex.split("/")
    # Initialize a variable to keep track of the longest path part
    longest_part = ""
    # Loop through each part
    for i, part in enumerate(parts):
        # Check if the part contains a regex by searching for any regex pattern
        regex_match = re.search(r"[\[\]\(\)\.\*\+\?\{\}\^\$\|\\\#]", part)
        if regex_match:
            # If it does, stop checking parts and return the longest part found so far and the rest of the string
            rest_of_string = "/".join(parts[i:])
            return longest_part, rest_of_string
        else:
            longest_part += part + "/"
    # If no part contains a regex, return the entire input string
    return string_with_regex