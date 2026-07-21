# 参与贡献

感谢参与 maimaiDX QueryBot。本项目基于 [Yuri-YuzuChaN/maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX) 修改，请在衍生发布中保留原项目来源和许可证声明。

## 开发流程

1. Fork 仓库并从 `main` 创建功能分支。
2. 只提交与本次变更有关的文件，不要提交 Token、二维码、数据库、日志或私钥。
3. 保持现有代码风格，并为可独立验证的逻辑补充测试。
4. 提交前运行：

   ```bash
   python scripts/test_footer_integrity.py
   python -m compileall -q .
   ```

5. Pull Request 中说明改动目的、用户影响、验证方式和兼容性风险。

## 页脚署名

固定项目署名由数字签名保护。请勿通过 Pull Request 修改签名载荷、签名、公钥或绕过校验。确需更新时，请先与维护者协调，由持有离线私钥的维护者重新签名。
