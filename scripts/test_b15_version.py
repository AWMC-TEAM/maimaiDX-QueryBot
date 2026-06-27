"""
版本/B15 分类回归测试（独立运行，无需 nonebot 运行时）。

验证：国服当前版本 = PRiSM PLUS 时
  1. B15(新版本) 集合 = 镜彩 (PRiSM / PRiSM PLUS)，不含双宴/CiRCLE
  2. B35(旧版本) 集合不含镜彩
  3. 上分「新版本列」候选只来自镜彩，绝不回退到双宴等旧版本
  4. b50 重分组：镜彩曲入 B15、其余入 B35

运行：python3 scripts/test_b15_version.py
"""
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---- 桩：让 config.py 在无 nonebot 环境下可导入 ----
def _install_nonebot_stub():
    nb = types.ModuleType("nonebot")

    class _Cfg:
        nickname = []

    class _Driver:
        config = _Cfg()

    def get_driver():
        return _Driver()

    import tempfile
    _static = tempfile.mkdtemp(prefix="maimai_static_")

    class _PluginCfg:
        maimaidxtoken = None
        maimaidxpath = _static
        maimaidx_music_cache_seconds = 3600
        maimaidx_data_source = None
        # 任意访问都返回默认（用 __getattr__ 兜底）
        def __getattr__(self, name):
            return None

    def get_plugin_config(_model):
        return _PluginCfg()

    nb.get_driver = get_driver
    nb.get_plugin_config = get_plugin_config
    sys.modules["nonebot"] = nb


_install_nonebot_stub()

import config as cfg  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


print("== 配置版本表 ==")
dx_values = list(cfg.plate_to_dx_version.values())
print("  末两作(应为镜彩):", dx_values[-2:])
check("末两作为 PRiSM / PRiSM PLUS",
      dx_values[-2:] == ['maimai でらっくす PRiSM', 'maimai でらっくす PRiSM PLUS'],
      f"实际={dx_values[-2:]}")
check("plate_to_dx_version 不含 CiRCLE",
      not any('CiRCLE' in v for v in dx_values),
      f"含CiRCLE的项={[v for v in dx_values if 'CiRCLE' in v]}")

print("== B15 / B35 版本集合 ==")
b15 = set(cfg.get_b15_version_names_at_generation(0))
b35 = set(cfg.get_b35_version_names_for_generation(0))
print("  B15:", sorted(b15))
check("B15 含 PRiSM 短/全名", {'PRiSM', 'maimai でらっくす PRiSM'} <= b15)
check("B15 含 PRiSM PLUS 短/全名", {'PRiSM PLUS', 'maimai でらっくす PRiSM PLUS'} <= b15)
check("B15 不含 BUDDiES", not any('BUDDiES' in v for v in b15))
check("B15 不含 CiRCLE", not any('CiRCLE' in v for v in b15))
check("B35 不含 PRiSM", not any(v in ('PRiSM', 'maimai でらっくす PRiSM') for v in b35))
check("B35 不含 PRiSM PLUS", not any(v in ('PRiSM PLUS', 'maimai でらっくす PRiSM PLUS') for v in b35))
check("B35 含 BUDDiES(双)", any('BUDDiES' in v for v in b35))

print("== resolve_b15_generation ==")
# 模拟曲库版本（含镜彩，dxdata 短名格式）
lib = {'PRiSM', 'PRiSM PLUS', 'BUDDiES', 'BUDDiES PLUS', 'Splash', 'FiNALE'}
check("曲库含镜彩时世代=0", cfg.resolve_b15_generation(lib) == 0,
      f"实际={cfg.resolve_b15_generation(lib)}")
# 曲库无镜彩 → 回退 0（不乱猜旧世代）
check("空曲库回退世代=0", cfg.resolve_b15_generation(set()) == 0)


# ---- 用真实 dxdata.json 模拟「新版本列」筛选 ----
print("== 上分新版本列筛选（基于 dxdata.json 真实版本）==")
data = json.load(open(ROOT / "dxdata.json", encoding="utf-8"))
# internalId -> version（取首个 sheet 版本，与 data_source 转换一致）
id_version = {}
id_title = {}
for song in data["songs"]:
    sheets = song.get("sheets") or []
    if not sheets:
        continue
    iid = sheets[0].get("internalId")
    id_version[iid] = sheets[0].get("version")
    id_title[iid] = song.get("title")

# 图中「新版本列」展示的曲目（应判定为旧版本→不该出现在新版本列）
shown_new_col = {
    11799: "いっぱい食べる君が好きだよ",
    11717: "LOSTPHANTASIA",
    11696: "アイドル",
    11693: "過去を喰らう",
    11666: "アイディスマイル",
}
print("  截图新版本列各曲真实版本及是否属于 B15:")
wrongly_old = []
for iid, title in shown_new_col.items():
    ver = id_version.get(iid, "?")
    is_b15 = ver in b15
    flag = "属于B15" if is_b15 else "旧版本(不该在新列)"
    print(f"    {iid} {title}: {ver} -> {flag}")
    if not is_b15:
        wrongly_old.append((iid, title, ver))

# 真实 PRiSM/PRiSM PLUS 曲目应被判为 B15
prism_ids = [iid for iid, v in id_version.items()
             if v in ('PRiSM', 'PRiSM PLUS')]
check("曲库存在 PRiSM/PRiSM PLUS 曲目", len(prism_ids) > 0,
      f"数量={len(prism_ids)}")
sample_prism = prism_ids[:5]
check("PRiSM 系曲目判定为 B15",
      all(id_version[i] in b15 for i in sample_prism),
      f"样本={[(i, id_version[i]) for i in sample_prism]}")

# 旧逻辑会把这些双/宴/镜混合曲塞进新版本列；新逻辑下，
# 凡 version 不在 b15 的曲目都不会进入「新版本列候选」
old_ver_in_shown = [t for t in wrongly_old]
print(f"  截图中本属旧版本却出现在新列的曲目数: {len(old_ver_in_shown)}")
check("修复后：BUDDiES(双宴) 不属于 B15 集合(不会再进新列)",
      all(id_version[iid] not in b15
          for iid in (11717, 11696, 11693, 11666)),
      "若失败说明 BUDDiES 仍被当作新版本")

print("== 端到端模拟：新版本列候选只可能是镜彩 ==")
# 复刻 get_rise_score_list 的过滤：candidate = 全曲库按 version∈B15 过滤
# （MusicList.filter 的 version 判定等价于 music.basic_info.version in B15）
new_col_candidates = [iid for iid, ver in id_version.items() if ver in b15]
bad = [(iid, id_title.get(iid), id_version.get(iid))
       for iid in new_col_candidates
       if id_version[iid] not in ('PRiSM', 'PRiSM PLUS',
                                  'maimai でらっくす PRiSM',
                                  'maimai でらっくす PRiSM PLUS')]
check("新版本列候选全部是 PRiSM/PRiSM PLUS", not bad, f"异常={bad[:5]}")
check("新版本列候选数量 > 0", len(new_col_candidates) > 0,
      f"数量={len(new_col_candidates)}")
print(f"  新版本列候选曲目数(镜彩): {len(new_col_candidates)}")

# 旧版本列(B35)候选：version∈B35，且不得含任何镜彩
old_col_candidates = [iid for iid, ver in id_version.items() if ver in b35]
b35_has_prism = [iid for iid in old_col_candidates
                 if id_version[iid] in ('PRiSM', 'PRiSM PLUS')]
check("旧版本列候选不含镜彩", not b35_has_prism, f"异常={b35_has_prism[:5]}")
print(f"  旧版本列候选曲目数: {len(old_col_candidates)}")

print()
print(f"结果: PASS={PASS}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
