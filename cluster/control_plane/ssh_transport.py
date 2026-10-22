"""Validated SSH routing; no config/key reads and no configurable shell flags."""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Optional


def valid_host(host):
    if not isinstance(host, str) or len(host) > 253 or host.count('@') > 1:
        return False
    account, separator, name = host.partition('@')
    if separator:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', account):
            return False
    else:
        name = account
    if ':' in name:
        if not re.fullmatch(r'[0-9A-Fa-f:]+', name):
            return False
        try:
            return ipaddress.ip_address(name).version == 6
        except ValueError:
            return False
    return re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,252}', name) is not None


@dataclass(frozen=True)
class SSHTransport:
    host: str
    user: str = ''
    port: Optional[int] = None
    identity_file: str = ''

    def __post_init__(self):
        if (not valid_host(self.host) or not isinstance(self.user, str)
                or (self.user and (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', self.user)
                                   or '@' in self.host))
                or (self.port is not None and (type(self.port) is not int or not 1 <= self.port <= 65535))
                or not isinstance(self.identity_file, str)):
            raise ValueError('Invalid SSH transport')
        key = self.identity_file
        if key and (len(key) > 4096 or not (key.startswith('/') or key.startswith('~/'))
                    or any(ord(char) < 32 or ord(char) == 127 for char in key)
                    or any(part in ('', '.', '..') for part in key.lstrip('/').split('/'))):
            raise ValueError('Invalid SSH identity path')

    def argv(self, executable='ssh'):
        # Identity is a single local argv value, never interpolated in a shell.
        args = [executable, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5']
        if self.identity_file:
            args += ['-i', os.path.expanduser(self.identity_file), '-o', 'IdentitiesOnly=yes']
        if self.port is not None:
            args += ['-p', str(self.port)]
        args.append(self.user + '@' + self.host if self.user else self.host)
        return args
