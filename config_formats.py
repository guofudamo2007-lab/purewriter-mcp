"""Small, validated edits to JSON/JSONC and TOML MCP configuration files."""
from __future__ import annotations

import copy
import json
import re
import tomllib


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('配置含重复键，无法安全更新。')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('配置含非标准 JSON 数字。')


DECODER = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def parse_jsonc(text):
    """Mask comments/trailing commas without moving any character offsets."""
    chars = list(text)
    if chars and chars[0] == '\ufeff':
        chars[0] = ' '
    i = 0
    while i < len(text):
        if text[i] == '"':
            _, i = DECODER.raw_decode(text, i)
        elif text[i:i + 2] in ('//', '/*'):
            if text[i:i + 2] == '//':
                end = text.find('\n', i)
                if end == -1:
                    end = len(text)
            else:
                end = text.find('*/', i + 2)
                if end == -1:
                    raise ValueError('JSONC 注释未闭合。')
                end += 2
            for at in range(i, end):
                if chars[at] not in '\r\n':
                    chars[at] = ' '
            i = end
        else:
            i += 1
    masked = ''.join(chars)
    i = 0
    while i < len(masked):
        if masked[i] == '"':
            _, i = DECODER.raw_decode(masked, i)
        elif masked[i] == ',':
            following = i + 1
            while following < len(masked) and masked[following].isspace():
                following += 1
            if following < len(masked) and masked[following] in '}]':
                previous = i - 1
                while previous >= 0 and masked[previous].isspace():
                    previous -= 1
                if previous < 0 or masked[previous] in '{[,:':
                    raise ValueError('JSONC 尾随逗号位置无效。')
                chars[i] = ' '
            i += 1
        else:
            i += 1
    masked = ''.join(chars)
    obj = DECODER.decode(masked)
    if not isinstance(obj, dict):
        raise ValueError('配置根节点必须是对象。')
    return obj, masked


def _members(masked, start):
    """Return object member value spans and the closing brace offset."""
    pos = start + 1
    members = {}
    while True:
        while masked[pos].isspace() or masked[pos] == ',':
            pos += 1
        if masked[pos] == '}':
            return members, pos
        key, pos = DECODER.raw_decode(masked, pos)
        while masked[pos].isspace() or masked[pos] == ':':
            pos += 1
        begin = pos
        _, pos = DECODER.raw_decode(masked, pos)
        members[key] = (begin, pos)


def _set_member(text, masked, start, key, value):
    members, end = _members(masked, start)
    newline = '\r\n' if '\r\n' in text else '\n'
    line_start = text.rfind('\n', 0, start) + 1
    parent_indent = re.match(r'[ \t]*', text[line_start:start]).group()
    indent = parent_indent + '  '
    encoded = json.dumps(value, ensure_ascii=False, indent=2).replace('\n', newline + indent)
    if key in members:
        begin, stop = members[key]
        return text[:begin] + encoded + text[stop:]
    insert = end
    closing_line = text.rfind('\n', 0, end) + 1
    if not text[closing_line:end].strip():
        insert = closing_line
    addition = ('' if insert and text[insert - 1] == '\n' else newline)
    addition += indent + json.dumps(key) + ': ' + encoded + newline
    if insert == end:
        addition += parent_indent
    edits = [(insert, addition)]
    if members:
        last_end = next(reversed(members.values()))[1]
        # Add the separator ahead of comments; do not count commas in comments.
        tail = re.sub(r'//[^\r\n]*|/\*[\s\S]*?\*/', '', text[last_end:end])
        if ',' not in tail:
            edits.append((last_end, ','))
    for at, addition in sorted(edits, key=lambda item: item[0], reverse=True):
        text = text[:at] + addition + text[at:]
    return text


def update_jsonc(text, root_key, entry):
    obj, masked = parse_jsonc(text)
    servers = obj.get(root_key, {})
    if not isinstance(servers, dict):
        raise ValueError('MCP 服务器列表必须是对象。')
    expected = copy.deepcopy(obj)
    expected.setdefault(root_key, {})['purewriter'] = entry
    if obj == expected:
        return text
    start = len(masked) - len(masked.lstrip())
    members, _ = _members(masked, start)
    if root_key in members:
        new = _set_member(text, masked, members[root_key][0], 'purewriter', entry)
    else:
        new = _set_member(text, masked, start, root_key, {'purewriter': entry})
    if parse_jsonc(new)[0] != expected:
        raise ValueError('无法只更新 purewriter，未写任何配置。')
    return new


def _toml_value(value):
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + ' = ' + _toml_value(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(_toml_value(v) for v in value) + ']'
    if isinstance(value, (str, bool, int, float)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    raise ValueError('purewriter 条目含不支持的 TOML 类型，请手动合并 --print-config 输出。')


def toml_snippet(entry):
    lines = ['[mcp_servers.purewriter]']
    lines.extend(json.dumps(k) + ' = ' + _toml_value(v) for k, v in entry.items() if k != 'env')
    lines.append('[mcp_servers.purewriter.env]')
    lines.extend(json.dumps(k) + ' = ' + _toml_value(v) for k, v in entry['env'].items())
    return '\n'.join(lines) + '\n'


def update_toml(text, entry):
    obj = tomllib.loads(text.lstrip('\ufeff'))
    expected = copy.deepcopy(obj)
    expected.setdefault('mcp_servers', {})['purewriter'] = entry
    if obj == expected:
        return text
    bom = '\ufeff' if text.startswith('\ufeff') else ''
    remaining = re.sub(r'(?ms)^\[mcp_servers\.purewriter(?:\.[^\]]+)?\][ \t]*\r?\n.*?(?=^\[|\Z)', '', text.lstrip('\ufeff'))
    new = bom + remaining.rstrip() + '\n\n' + toml_snippet(entry)
    # Fail closed for quoted/inline table layouts the narrow edit cannot handle.
    if tomllib.loads(new.lstrip('\ufeff')) != expected:
        raise ValueError('TOML 无法只更新 purewriter，请手动合并 --print-config 输出。')
    return new
