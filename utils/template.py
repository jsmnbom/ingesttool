

import copy
import io
import re

from datetime import datetime
import traceback

from utils.utils import UnionDict

EVAL_OPEN = '{{'
EVAL_CLOSE = '}}'

EXEC_OPEN = '{%'
EXEC_CLOSE = '%}'

class Source(str):
    def __getitem__(self, __i) -> str:
        if not isinstance(__i, slice):
            __i = slice(__i, __i+1)
        __i = slice(max(__i.start, 0), min(__i.stop, len(self)))
        return Source(super().__getitem__(__i))

class ExecDict(dict):
    def __init__(self, dict):
        self.dict = dict
        self.result = {}
        
    def __getitem__(self, key):
        return self.dict[key]
    
    def __setitem__(self, key, value):
        self.result[key] = value


GLOBALS = {'datetime': datetime}

class Template:
    def __init__(self, template: str):
        tokens = self.lex(template)
        self.ast = self.parse(tokens)

    def _process(self, context: dict):
        new_ast = []
        if context is None:
            context = {}
        local_context = {}
        for node in self.ast:
            if node['type'] == 'eval':
                try:
                    # print('qqq', context['exif'])
                    result = eval(node['value'], GLOBALS, UnionDict(context, local_context))
                    if isinstance(result, Template):
                        result._process(UnionDict(context, local_context))
                        new_ast.extend(result.ast)
                        continue
                    else:
                        new_ast.append({
                            'type': 'text',
                            'value': result,
                        })
                        continue
                except Exception as e:
                    traceback.print_exc()
                    continue
            elif node['type'] == 'exec':
                # print('qqq', context['ffprobe'])
                try:

                    c = ExecDict(UnionDict(context, local_context))
                    exec(node['value'], GLOBALS, c)
                    # print(c.result)
                    local_context = c.result
                except Exception as e:
                    traceback.print_exc()
                    continue
            elif node['type'] == 'text':
                val = node['value']
                val = re.sub(r'\n|\r|((?<!\\) )', '', val, flags=re.MULTILINE)
                val = re.sub(r'\\ ', ' ', val, flags=re.MULTILINE)
                new_ast.append({
                    'type': 'text',
                    'value': val,
                })

        self.ast = new_ast

    def render(self, context: dict = None):
        self._process({} if context is None else context)
        with io.StringIO() as memfd:
            for node in self.ast:
                if node['type'] == 'text':
                    memfd.write(node['value'])
                else:
                    raise Exception('Not fully processed or unknown node type.')
            return memfd.getvalue()

    def lex(self, source):
        source = Source(source)
        tokens = []
        current = ''
        cursor = 0
        while cursor < len(source):
            for tag in [EVAL_OPEN, EVAL_CLOSE, EXEC_OPEN, EXEC_CLOSE]:
                if source[cursor:cursor+len(tag)] == tag:
                    if current:
                        tokens.append(current)
                        current = ''

                    tokens.append(tag)
                    cursor += len(tag)
                    break
            else:
                current += source[cursor]
                cursor += 1

        if current:
            tokens.append(current)

        return tokens
    

    def parse(self,tokens):
        cursor = 0
        ast = []
        while cursor < len(tokens):
            value = tokens[cursor]
            if value == EVAL_OPEN:
                if tokens[cursor+2] != EVAL_CLOSE:
                    raise Exception('Expected closing tag')

                node_ast = compile(tokens[cursor+1].strip(), '<ast>', mode='eval')
                ast.append({
                    'type': 'eval',
                    'value': node_ast,
                })
                cursor += 3
                continue

            if value == EVAL_OPEN:
                raise Exception('Expected opening tag')
            
            if value == EXEC_OPEN:
                if tokens[cursor+2] != EXEC_CLOSE:
                    raise Exception('Expected closing tag')

                node_ast = compile(tokens[cursor+1].strip(), '<ast>', mode='exec')
                ast.append({
                    'type': 'exec',
                    'value': node_ast,
                })
                cursor += 3
                continue

            if value == EXEC_OPEN:
                raise Exception('Expected opening tag')
            
            ast.append({
                'type': 'text',
                'value': value,
            })
            cursor += 1

        return ast