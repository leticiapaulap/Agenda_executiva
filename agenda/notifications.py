"""Toast opcional via WinRT; o aplicativo também exibe o lembrete localmente.

O conteúdo do usuário é XML transportado em base64, nunca código PowerShell.
"""
import base64
import logging
import os
import subprocess
import xml.etree.ElementTree as ET


def notification_script(event):
    toast = ET.Element('toast', duration='long')
    binding = ET.SubElement(ET.SubElement(toast, 'visual'), 'binding', template='ToastGeneric')
    ET.SubElement(binding, 'text').text = 'Lembrete · Agenda Executiva'
    ET.SubElement(binding, 'text').text = str(event['title'])[:180]
    ET.SubElement(binding, 'text').text = f'{event["event_date"]} às {event["event_time"]} · {event["location"]}'[:240]
    payload = base64.b64encode(ET.tostring(toast, encoding='utf-8')).decode('ascii')
    script = '''$ErrorActionPreference = 'Stop'
try {
  $key = 'HKCU:\\Software\\Classes\\AppUserModelId\\AgendaExecutiva.Desktop'
  New-Item -Path $key -Force | Out-Null
  New-ItemProperty -Path $key -Name DisplayName -Value 'Agenda Executiva' -PropertyType String -Force | Out-Null
  [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
  [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
  $xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
  $xml.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PAYLOAD')))
  $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
  [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('AgendaExecutiva.Desktop').Show($toast)
  exit 0
} catch { exit 1 }
'''
    return script.replace('PAYLOAD', payload)


def send_native(event):
    if os.name != 'nt':
        return False
    try:
        encoded = base64.b64encode(notification_script(event).encode('utf-16le')).decode('ascii')
        result = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                 '-EncodedCommand', encoded], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=20, check=False)
        if result.returncode:
            logging.warning('Windows não aceitou o toast; lembrete exibido na aplicação.')
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        logging.exception('Toast indisponível; lembrete exibido na aplicação.')
        return False
