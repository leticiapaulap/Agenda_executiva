"""Verifica o transporte seguro de dados para o processo nativo sem enviar toast."""
import base64
import re
import unittest
from unittest.mock import patch, Mock
import xml.etree.ElementTree as ET

from agenda.notifications import notification_script, send_native


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.event = {'title': 'Reunião $(Write-Output teste) " </text> & orçamento',
                      'event_date': '2026-11-04', 'event_time': '09:00',
                      'location': 'Sala São Paulo ` $variavel'}

    def test_content_is_xml_data_and_never_interpolated_script(self):
        script = notification_script(self.event)
        self.assertNotIn(self.event['title'], script)
        self.assertNotIn('Write-Output', script)
        encoded = re.search(r"FromBase64String\('([^']+)'\)", script).group(1)
        root = ET.fromstring(base64.b64decode(encoded))
        texts = [node.text for node in root.findall('.//text')]
        self.assertIn(self.event['title'], texts)
        self.assertIn(self.event['location'], texts[2])

    @patch('agenda.notifications.subprocess.run')
    @patch('agenda.notifications.os.name', 'nt')
    def test_native_process_is_hidden_and_has_timeout(self, run):
        run.return_value = Mock(returncode=0)
        self.assertTrue(send_native(self.event))
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(command[-2], '-EncodedCommand')
        script = base64.b64decode(command[-1]).decode('utf-16le')
        self.assertIn('ToastNotificationManager', script)
        self.assertEqual(kwargs['timeout'], 20)
        self.assertGreater(kwargs['creationflags'], 0)
        self.assertNotIn('shell', kwargs)

    @patch('agenda.notifications.subprocess.run', side_effect=OSError('PowerShell indisponível'))
    @patch('agenda.notifications.os.name', 'nt')
    def test_unavailable_native_delivery_returns_false(self, run):
        with self.assertLogs(level='ERROR'):
            self.assertFalse(send_native(self.event))


if __name__ == '__main__':
    unittest.main()
