"""
幻之成绩生成逻辑测试脚本。
在项目根目录运行: python3 scripts/test_phantom_score.py
"""
import sys
import os

# 把项目根目录加入 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 先 mock 掉一些不需要的模块
import types
nonebot_mod = types.ModuleType('nonebot')
nonebot_mod.get_driver = lambda: types.SimpleNamespace(config=types.SimpleNamespace(nickname=['test']))
nonebot_mod.get_plugin_config = lambda cls: cls(maimaidxpath=os.path.join(os.path.dirname(__file__), '..', 'static'))
sys.modules['nonebot'] = nonebot_mod

# 初始化配置
from config import maiconfig, mai
from libraries.maimaidx_music import mai as mai_instance

print('正在加载曲库...')
import asyncio
async def init():
    await mai_instance.get_music()
    print(f'曲库加载完成，共 {len(mai_instance.total_list)} 首曲目')

asyncio.run(init())

from libraries.maimaidx_phantom_score import generate_phantom_score, format_phantom_score_text

print('\n=== 测试 1：目标 15000 ===')
b35, b15, actual = generate_phantom_score(15000)
print(f'B35: {len(b35)} 首, B15: {len(b15)} 首, 实际 Rating: {actual}')
b35_total = sum(c.ra for c in b35)
b15_total = sum(c.ra for c in b15)
print(f'B35 合计: {b35_total}, B15 合计: {b15_total}, 总计: {b35_total + b15_total}')
print(f'目标: 15000, 误差: {actual - 15000:+d}')

# 打印前 5 首
print('\nB35 前 5 首:')
for c in b35[:5]:
    fc_fs = c.fc or '-'
    if c.fs:
        fc_fs += f'/{c.fs}'
    print(f'  {c.title:20s} ds={c.ds:.1f} ach={c.achievements:.4f}% rate={c.rate:5s} fc={fc_fs:6s} ra={c.ra}')

print('\nB15 前 5 首:')
for c in b15[:5]:
    fc_fs = c.fc or '-'
    if c.fs:
        fc_fs += f'/{c.fs}'
    print(f'  {c.title:20s} ds={c.ds:.1f} ach={c.achievements:.4f}% rate={c.rate:5s} fc={fc_fs:6s} ra={c.ra}')

print('\n=== 测试 2：目标 12000 ===')
b35, b15, actual = generate_phantom_score(12000)
print(f'目标: 12000, 实际: {actual}, 误差: {actual - 12000:+d}')

print('\n=== 测试 3：目标 16000 ===')
b35, b15, actual = generate_phantom_score(16000)
print(f'目标: 16000, 实际: {actual}, 误差: {actual - 16000:+d}')
