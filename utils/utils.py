class UnionDict(dict):
    def __init__(self, *dicts):
        self.dicts = dicts

    def __getitem__(self, key):
        for d in self.dicts:
            try:
                return d[key]
            except KeyError:
                continue
        raise KeyError(key)