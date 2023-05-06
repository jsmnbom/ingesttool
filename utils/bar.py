from tqdm import tqdm
from functools import partial


simple_bar_format="{desc:<35} {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}"
bar_format="{desc:<35} {percentage:3.0f}%|{bar}{r_bar}"

bytes_bar = partial(tqdm, bar_format=bar_format, unit='B', unit_scale=True, unit_divisor=1024)
simple_bar = partial(tqdm, bar_format=simple_bar_format)