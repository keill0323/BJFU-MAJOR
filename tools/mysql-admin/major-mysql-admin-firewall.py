"""Own only the IPv4 INPUT rule for the Major management relay on TCP 43306.

The socket must require this successful oneshot before it begins listening.
The existing Docker loopback mapping and all unrelated rules remain in place.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess


CHAIN = 'MAJOR-MYSQL-ADMIN'
MARKER = 'bjfu-major-mysql-admin'
PORT = '43306'
CONFIG = Path('/etc/major-mysql-admin/allowed-sources.json')
IPTABLES = '/usr/sbin/iptables'
RESTORE = '/usr/sbin/iptables-restore'


def command(arguments, *, check=True, data=None):
    result = subprocess.run(arguments, input=data, capture_output=True, text=True, timeout=15)
    if check and result.returncode:
        raise RuntimeError('firewall_command_failed')
    return result


def hook(operation):
    return [IPTABLES, '-w', '5', operation, 'INPUT', '-p', 'tcp', '--dport', PORT,
            '-m', 'comment', '--comment', MARKER, '-j', CHAIN]


def hook_exists():
    result = command(hook('-C'), check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError('firewall_check_failed')
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remove', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError('root_required')
    listing = command([IPTABLES, '-w', '5', '-S']).stdout.splitlines()
    existing = [line for line in listing if line == '-N ' + CHAIN or line.startswith('-A ' + CHAIN + ' ')]
    if existing and not any(MARKER in line for line in existing):
        raise RuntimeError('unowned_chain_refused')
    if args.remove:
        if existing:
            while hook_exists():
                command(hook('-D'))
            command([IPTABLES, '-w', '5', '-F', CHAIN])
            command([IPTABLES, '-w', '5', '-X', CHAIN])
        print(json.dumps({'removed': True, 'port': int(PORT)}))
        return

    config = json.loads(CONFIG.read_text())
    sources = config.get('allowed_sources', [])
    if not isinstance(sources, list) or not 1 <= len(sources) <= 16:
        raise RuntimeError('nonempty_source_whitelist_required')
    networks = sorted({str(ipaddress.IPv4Network(value, strict=True)) for value in sources})
    if any(ipaddress.IPv4Network(value).prefixlen != 32 for value in networks):
        raise RuntimeError('individual_ipv4_sources_required')
    rules = ['*filter', ':' + CHAIN + ' - [0:0]', '-F ' + CHAIN,
             '-A ' + CHAIN + ' -s 127.0.0.0/8 -j ACCEPT']
    rules.extend('-A ' + CHAIN + ' -s ' + value + ' -j ACCEPT' for value in networks)
    rules.extend(['-A ' + CHAIN + ' -m comment --comment ' + MARKER + ' -j DROP', 'COMMIT', ''])
    # Atomic replacement: no intermediate empty chain that would return ACCEPT.
    command([RESTORE, '-w', '5', '--noflush'], data='\n'.join(rules))
    if not hook_exists():
        command(hook('-I'))
    print(json.dumps({'configured': True, 'port': int(PORT), 'allowed_sources': len(networks)}))


if __name__ == '__main__':
    main()
