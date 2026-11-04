"""Tokens visuais compartilhados pela interface desktop."""
import sys
from pathlib import Path

import customtkinter as ctk
from customtkinter.windows.widgets.scaling import CTkScalingBaseClass

BG = ('#F3F6FB', '#111B2B')
CARD = ('#FFFFFF', '#1B293C')
INK = ('#172B47', '#E7EEF8')
MUTED = ('#63758C', '#A9B8CD')
BORDER = ('#E0E7F1', '#304159')
SOFT = ('#EDF3FD', '#233C5F')
BLUE = '#285FD0'
NAV = '#11243D'
GREEN = ('#107A66', '#63D5B7')
RED = ('#B43B50', '#FF99A8')
PRIORITY_COLORS = {'Baixa': '#688196', 'Normal': '#285FD0', 'Alta': '#B77216', 'Urgente': '#BB3B52'}


class OptionMenu(ctk.CTkOptionMenu):
    """Compensa a limpeza incompleta de DropdownMenu no CustomTkinter 5.2.2.

    A biblioteca remove os callbacks de aparência, mas esquece o callback de
    escala do menu filho. Sem esta limpeza, trocar escala após navegar tenta
    atualizar menus Tcl já destruídos. Mantemos a correção local ao projeto.
    """
    def destroy(self):
        CTkScalingBaseClass.destroy(self._dropdown_menu)
        super().destroy()


def asset_path(name):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return root / 'assets' / name


def font(size=14, bold=False):
    return ctk.CTkFont(family='Segoe UI', size=size, weight='bold' if bold else 'normal')


def label(parent, text, size=14, bold=False, color=INK, **kwargs):
    return ctk.CTkLabel(parent, text=text, font=font(size, bold), text_color=color, **kwargs)


def button(parent, text, command, secondary=False, **kwargs):
    return ctk.CTkButton(parent, text=text, command=command, font=font(13, True),
                         height=38, corner_radius=9,
                         fg_color=SOFT if secondary else BLUE,
                         text_color=INK if secondary else 'white',
                         hover_color=('#DCE8FC', '#345378') if secondary else '#1E4EAE', **kwargs)


def card(parent, **kwargs):
    return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14,
                       border_width=1, border_color=BORDER, **kwargs)
