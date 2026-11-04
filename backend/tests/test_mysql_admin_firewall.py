"""Isolated firewall failure tests; never execute iptables or read host config."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'mysql_admin_firewall', ROOT / 'tools/mysql-admin/major-mysql-admin-firewall.py')
firewall = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(firewall)

OWNED = '\n'.join([
    '-P INPUT ACCEPT',
    '-N MAJOR-MYSQL-ADMIN',
    '-A MAJOR-MYSQL-ADMIN -m comment --comment bjfu-major-mysql-admin -j DROP',
    '-N OTHER-SERVICE',
    '-A OTHER-SERVICE -j ACCEPT',
])
HOOK = ['INPUT', '-p', 'tcp', '--dport', '43306', '-m', 'comment',
        '--comment', 'bjfu-major-mysql-admin', '-j', 'MAJOR-MYSQL-ADMIN']


def result(code=0, stdout=''):
    return subprocess.CompletedProcess([], code, stdout, '')


class MySQLAdminFirewallTests(unittest.TestCase):
    def invoke(self, *, config=None, listing=OWNED, inspection_code=0,
               responses=(), remove=False):
        self.process = Mock(side_effect=[result(inspection_code, listing), *responses])
        source_file = Mock()
        source_file.read_text.return_value = json.dumps(
            {'allowed_sources': ['203.0.113.9/32']} if config is None else config)
        self.output = io.StringIO()
        with patch.object(firewall.os, 'geteuid', return_value=0, create=True), \
                patch.object(firewall, 'CONFIG', source_file), \
                patch.object(firewall.subprocess, 'run', self.process), \
                patch.object(sys, 'argv', ['firewall', *(['--remove'] if remove else [])]), \
                redirect_stdout(self.output):
            firewall.main()

    def mutations(self):
        return [call for call in self.process.call_args_list
                if call.args[0][0] == firewall.RESTORE
                or any(flag in call.args[0] for flag in ('-I', '-D', '-F', '-X'))]

    def test_invalid_whitelists_are_rejected_without_firewall_mutations(self):
        invalid = [
            {}, {'allowed_sources': []}, {'allowed_sources': '203.0.113.9/32'},
            {'allowed_sources': ['0.0.0.0/0']},
            {'allowed_sources': ['203.0.113.0/24']},
            {'allowed_sources': ['::1/128']},
            {'allowed_sources': ['203.0.113.9/32\n-A INPUT -j ACCEPT']},
            {'allowed_sources': ['203.0.113.9/32'] * 17},
        ]
        for config in invalid:
            with self.subTest(config=config):
                with self.assertRaises((RuntimeError, ValueError)):
                    self.invoke(config=config)
                self.assertEqual(self.mutations(), [])

    def test_owned_chain_is_replaced_in_one_transaction_before_precise_hook(self):
        self.invoke(
            config={'allowed_sources': ['203.0.113.9/32', '198.51.100.4', '203.0.113.9/32']},
            responses=[result(), result(1), result()])
        writes = self.mutations()
        self.assertEqual(len(writes), 2)
        self.assertEqual(writes[0].args[0], [firewall.RESTORE, '-w', '5', '--noflush'])
        transaction = writes[0].kwargs['input'].splitlines()
        self.assertEqual(transaction[0], '*filter')
        self.assertEqual(transaction[-1], 'COMMIT')
        self.assertEqual(transaction.count('-F MAJOR-MYSQL-ADMIN'), 1)
        self.assertIn('-A MAJOR-MYSQL-ADMIN -s 127.0.0.0/8 -j ACCEPT', transaction)
        self.assertIn('-A MAJOR-MYSQL-ADMIN -s 198.51.100.4/32 -j ACCEPT', transaction)
        self.assertEqual(transaction.count(
            '-A MAJOR-MYSQL-ADMIN -s 203.0.113.9/32 -j ACCEPT'), 1)
        self.assertEqual(transaction[-2],
                         '-A MAJOR-MYSQL-ADMIN -m comment --comment bjfu-major-mysql-admin -j DROP')
        self.assertFalse(any('INPUT' in line or 'OTHER-SERVICE' in line for line in transaction))
        self.assertEqual(writes[1].args[0], [firewall.IPTABLES, '-w', '5', '-I', *HOOK])
        self.assertEqual(json.loads(self.output.getvalue())['allowed_sources'], 2)

    def test_unowned_chain_or_failed_inspection_never_mutates(self):
        unowned = '\n'.join([
            '-N MAJOR-MYSQL-ADMIN', '-A MAJOR-MYSQL-ADMIN -j ACCEPT',
            '-N OTHER-SERVICE',
            '-A OTHER-SERVICE -m comment --comment bjfu-major-mysql-admin -j ACCEPT',
        ])
        for remove in (False, True):
            for listing, code, error in [(unowned, 0, 'unowned_chain_refused'),
                                         ('', 4, 'firewall_command_failed')]:
                with self.subTest(remove=remove, inspection_code=code):
                    with self.assertRaisesRegex(RuntimeError, error):
                        self.invoke(listing=listing, inspection_code=code, remove=remove)
                    self.assertEqual(self.mutations(), [])

    def test_failed_restore_or_uncertain_hook_check_never_inserts_input_rule(self):
        for responses, error in [([result(4)], 'firewall_command_failed'),
                                 ([result(), result(4)], 'firewall_check_failed')]:
            with self.subTest(error=error):
                with self.assertRaisesRegex(RuntimeError, error):
                    self.invoke(listing='-P INPUT ACCEPT', responses=responses)
                self.assertEqual(len(self.mutations()), 1)
                self.assertEqual(self.mutations()[0].args[0][0], firewall.RESTORE)

    def test_remove_deletes_only_exact_owned_hooks_and_owned_chain(self):
        unrelated_hook = '-A INPUT -p tcp --dport 43306 -j OTHER-SERVICE'
        self.invoke(
            listing=OWNED + '\n' + unrelated_hook,
            responses=[result(), result(), result(), result(), result(1), result(), result()],
            remove=True)
        writes = [call.args[0] for call in self.mutations()]
        self.assertEqual(writes, [
            [firewall.IPTABLES, '-w', '5', '-D', *HOOK],
            [firewall.IPTABLES, '-w', '5', '-D', *HOOK],
            [firewall.IPTABLES, '-w', '5', '-F', 'MAJOR-MYSQL-ADMIN'],
            [firewall.IPTABLES, '-w', '5', '-X', 'MAJOR-MYSQL-ADMIN'],
        ])
        self.assertFalse(any('OTHER-SERVICE' in arguments for arguments in writes))
        self.assertTrue(json.loads(self.output.getvalue())['removed'])


if __name__ == '__main__':
    unittest.main()
