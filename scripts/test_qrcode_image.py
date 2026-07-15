"""图片二维码识别回归测试（需安装项目依赖 zxing-cpp）。"""

import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import zxingcpp

from libraries.maimaidx_qrcode_util import (
    _safe_remote_image_url,
    decode_sgwcmaid_qrcode_image,
    extract_sgwcmaid_from_image_segments,
)


SGWCMAID = (
    "SGWCMAID26071514110240B055073BAFB054595882DF610F3D7CF2EECF402B9302ECF55C61972E55C7B8"
)


def qr_png(text: str) -> bytes:
    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    image = Image.fromarray(barcode.to_image(scale=5))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


raw_png = qr_png(SGWCMAID)
assert decode_sgwcmaid_qrcode_image(raw_png) == SGWCMAID

official_url = (
    "https://wq.wahlap.net/qrcode/img/"
    "MAID26071514110240B055073BAFB054595882DF610F3D7CF2EECF402B9302ECF55C61972E55C7B8.png?v"
)
assert decode_sgwcmaid_qrcode_image(qr_png(official_url)) == SGWCMAID
assert decode_sgwcmaid_qrcode_image(qr_png("https://example.com/not-maimai")) is None

segment = SimpleNamespace(
    type="image",
    data={"file": "base64://" + base64.b64encode(raw_png).decode()},
)
assert asyncio.run(extract_sgwcmaid_from_image_segments([segment])) == SGWCMAID
assert _safe_remote_image_url("https://gchat.qpic.cn/example.png")
assert not _safe_remote_image_url("http://127.0.0.1/secret.png")
assert not _safe_remote_image_url("http://localhost/secret.png")

print("image QR recognition tests: ok")
