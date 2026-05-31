import base64
from io import BytesIO
from typing import Tuple, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..config import SHANGGUMONO, Path, coverdir


class DrawText:

    def __init__(self, image: ImageDraw.ImageDraw, font: Path) -> None:
        self._img = image
        self._font = str(font)

    def get_box(self, text: str, size: int) -> Tuple[float, float, float, float]:
        return ImageFont.truetype(self._font, size).getbbox(text)

    def draw(
        self,
        pos_x: int,
        pos_y: int,
        size: int,
        text: Union[str, int, float],
        color: Tuple[int, int, int, int] = (255, 255, 255, 255),
        anchor: str = 'lt',
        stroke_width: int = 0,
        stroke_fill: Tuple[int, int, int, int] = (0, 0, 0, 0),
        multiline: bool = False
    ) -> None:
        font = ImageFont.truetype(self._font, size)
        if multiline:
            self._img.multiline_text(
                (pos_x, pos_y), 
                str(text), 
                color, 
                font, 
                anchor, 
                stroke_width=stroke_width, 
                stroke_fill=stroke_fill
            )
        else:
            self._img.text(
                (pos_x, pos_y), 
                str(text), 
                color, 
                font, 
                anchor, 
                stroke_width=stroke_width, 
                stroke_fill=stroke_fill
            )


def tricolor_gradient(
    width: int, 
    height: int, 
    color1: Tuple[int, int, int] = (124, 129, 255), 
    color2: Tuple[int, int, int] = (193, 247, 225), 
    color3: Tuple[int, int, int] = (255, 255, 255)
) -> Image.Image:
    """绘制渐变色"""
    array = np.zeros((height, width, 3), dtype=np.uint8)
    
    for y in range(height):
        if y < height * 0.4:
            ratio = y / (height * 0.4)
            color = (1 - ratio) * np.array(color1) + ratio * np.array(color2)
        else:
            ratio = (y - height * 0.4) / (height * 0.6)
            color = (1 - ratio) * np.array(color2) + ratio * np.array(color3)
        array[y, :] = np.clip(color, 0, 255)
    
    image = Image.fromarray(array).convert('RGBA')
    return image


def rounded_corners(
    image: Image.Image,
    radius: int, 
    corners: Tuple[bool, bool, bool, bool] = (False, False, False, False)
) -> Image.Image:
    """
    绘制圆角
    
    Params:
        `image`: `PIL.Image.Image`
        `radius`: 圆角半径
        `corners`: 四个角是否绘制圆角，分别是左上、右上、右下、左下
    Returns:
        `PIL.Image.Image`
    """
    mask = Image.new('L', image.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, image.size[0], image.size[1]), radius, fill=255, corners=corners)

    new_im = ImageOps.fit(image, mask.size)
    new_im.putalpha(mask)

    return new_im


def music_picture(music_id: Union[int, str]) -> Path:
    """
    获取谱面图片路径
    
    查找顺序：
    1. 直接查找 {music_id}.png
    2. 如果是宴谱(>100000)，尝试 {music_id - 100000}.png
    3. 如果是 DX/SD 转换范围，尝试 ±10000
    4. 尝试 .jpg 格式
    5. 返回默认占位图
    
    Params:
        `music_id`: 谱面 ID
    Returns:
        `Path`
    """
    original_id = music_id
    music_id = int(music_id)
    
    # 1. 直接查找 PNG
    if (_path := coverdir / f'{music_id}.png').exists():
        return _path
    
    # 2. 宴谱处理 (ID >= 100000)
    if music_id >= 100000:
        base_id = music_id - 100000
        if (_path := coverdir / f'{base_id}.png').exists():
            return _path
        if (_path := coverdir / f'{base_id}.jpg').exists():
            return _path
    
    # 3. DX/SD 转换 (1000-11000 范围)
    if 1000 < music_id < 10000:
        # SD 谱面，尝试找 DX 版本
        if (_path := coverdir / f'{music_id + 10000}.png').exists():
            return _path
        if (_path := coverdir / f'{music_id + 10000}.jpg').exists():
            return _path
    elif 10000 < music_id <= 11000:
        # DX 谱面，尝试找 SD 版本
        if (_path := coverdir / f'{music_id - 10000}.png').exists():
            return _path
        if (_path := coverdir / f'{music_id - 10000}.jpg').exists():
            return _path
    
    # 4. 尝试 JPG 格式
    if (_path := coverdir / f'{music_id}.jpg').exists():
        return _path
    
    # 5. 默认占位封面
    for _fallback in ('11000.png', '0.png', '11000.jpg', '0.jpg'):
        if (_path := coverdir / _fallback).exists():
            return _path
    
    # 最后返回 0.png 路径（即使不存在，让调用方处理错误）
    return coverdir / '0.png'


def text_to_image(text: str) -> Image.Image:
    font = ImageFont.truetype(str(SHANGGUMONO), 24)
    padding = 10
    margin = 4
    lines = text.strip().split('\n')
    max_width = 0
    b = 0
    for line in lines:
        l, t, r, b = font.getbbox(line)
        max_width = max(max_width, r)
    wa = max_width + padding * 2
    ha = b * len(lines) + margin * (len(lines) - 1) + padding * 2
    im = Image.new('RGB', (wa, ha), color=(255, 255, 255))
    draw = ImageDraw.Draw(im)
    for index, line in enumerate(lines):
        draw.text((padding, padding + index * (margin + b)), line, font=font, fill=(0, 0, 0))
    return im


def text_to_bytes_io(text: str) -> BytesIO:
    bio = BytesIO()
    text_to_image(text).save(bio, format='PNG')
    bio.seek(0)
    return bio


def image_to_base64(img: Image.Image, format='PNG') -> str:
    output_buffer = BytesIO()
    img.save(output_buffer, format)
    byte_data = output_buffer.getvalue()
    base64_str = base64.b64encode(byte_data).decode()
    return 'base64://' + base64_str