"""Entrada da Agenda Executiva desktop (Python 3.14 / Windows)."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


def main():
    data_dir = Path.home() / 'AgendaExecutiva'
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
            handlers=[RotatingFileHandler(data_dir / 'agenda.log', maxBytes=1_000_000,
                                         backupCount=2, encoding='utf-8')])
        from agenda import ui
        logging.info('Starting Agenda Executiva frozen=%s executable=%s entry=%s ui_module=%s',
                     getattr(sys, 'frozen', False), sys.executable, Path(__file__).resolve(),
                     Path(ui.__file__).resolve())
        app = ui.AgendaApp()
        logging.info('Agenda ready pid=%s db=%s', os.getpid(), app.db.path)

        def report_callback_exception(exc_type, error, traceback):
            logging.error('Unhandled Tk callback exception', exc_info=(exc_type, error, traceback))
            app.show_error(error)

        app.report_callback_exception = report_callback_exception
        app.mainloop()
        return 0
    except Exception as error:
        logging.exception('Erro ao iniciar a agenda')
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        detail = ('Instale as dependências com: .venv\\Scripts\\python.exe -m pip install -r requirements.txt'
                  if isinstance(error, ModuleNotFoundError) else str(error))
        messagebox.showerror('Agenda Executiva', f'Não foi possível iniciar a agenda.\n\n{detail}\n\n'
                            'Consulte o arquivo agenda.log na pasta dos dados.', parent=root)
        root.destroy()
        return 1


if __name__ == '__main__':
    sys.exit(main())
