"""Private, opaque receipt storage. Only verified and re-encoded raster images."""
from io import BytesIO
from pathlib import Path
import os
import re
import warnings
from uuid import uuid4

from PIL import Image, UnidentifiedImageError


MAX_RECEIPT_BYTES = 5 * 1024 * 1024
MAX_RECEIPT_PIXELS = 20_000_000
ID_PATTERN = re.compile(r'^[a-f0-9]{32}\.(?:png|jpg)$')


class ReceiptStore:
    def __init__(self, root):
        if not root:
            raise ValueError('Configure o armazenamento privado de comprovantes.')
        self.root = Path(root).expanduser().resolve()

    def save(self, stream, filename):
        suffix = Path(filename or '').suffix.lower()
        if suffix not in ('.png', '.jpg', '.jpeg'):
            raise ValueError('Comprovantes devem ser imagens PNG ou JPEG de até 5 MB.')
        payload = stream.read(MAX_RECEIPT_BYTES + 1)
        if not payload or len(payload) > MAX_RECEIPT_BYTES:
            raise ValueError('Comprovantes devem ter até 5 MB.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(payload)) as image:
                    expected = 'PNG' if suffix == '.png' else 'JPEG'
                    if image.format != expected or image.width * image.height > MAX_RECEIPT_PIXELS:
                        raise ValueError('Imagem incompatível ou muito grande (máximo 20 megapixels).')
                    image.verify()
                with Image.open(BytesIO(payload)) as image:
                    clean = Image.new('RGB', image.size, 'white')
                    if image.mode in ('RGBA', 'LA') or 'transparency' in image.info:
                        rgba = image.convert('RGBA')
                        clean.paste(rgba, mask=rgba.getchannel('A'))
                    else:
                        clean.paste(image.convert('RGB'))
                    output = BytesIO()
                    clean.save(output, format=expected)
                    content = output.getvalue()
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
                Image.DecompressionBombWarning):
            raise ValueError('Comprovante inválido. Envie uma imagem PNG ou JPEG.') from None
        if len(content) > MAX_RECEIPT_BYTES:
            raise ValueError('A imagem processada excede 5 MB. Reduza suas dimensões.')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt_id = uuid4().hex + ('.png' if expected == 'PNG' else '.jpg')
        path = self.root / receipt_id
        try:
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return {'id': receipt_id, 'name': 'comprovante' + path.suffix,
                'mime': 'image/png' if expected == 'PNG' else 'image/jpeg'}

    def open(self, receipt_id):
        if not isinstance(receipt_id, str) or not ID_PATTERN.fullmatch(receipt_id):
            raise ValueError('Identificador de comprovante inválido.')
        path = self.root / receipt_id
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError('Comprovante não encontrado.')
        return path

    def delete(self, receipt_id):
        try:
            self.open(receipt_id).unlink()
        except FileNotFoundError:
            pass
