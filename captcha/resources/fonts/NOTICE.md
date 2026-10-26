# 点选验证码字体（OFL 1.1）

- **字体**：Noto Sans SC（Regular）
- **版权**：Copyright 2014-2021 Adobe (http://www.adobe.com/), with Reserved Font Name 'Source'.
- **许可**：SIL Open Font License 1.1（全文见同目录 `OFL.txt`）
- **来源**：Google Fonts 分发的 Noto Sans SC Regular TTF
- **子集化**：使用 fontTools `pyftsubset` 按 GB2312 一级字库（3755 个常用汉字）+ 字母数字裁剪，
  `--layout-features='' --no-hinting`，产物 `NotoSansSC-Captcha.ttf`（约 1.1MB）
- **用途**：交互式验证码「文字点选」题型绘制题干汉字与提示条
- **维护**：替换字体时必须保持 OFL 兼容并更新本文件；字体缺失时
  `captcha/backends/wordclick.py` 抛 `ImproperlyConfigured`（服务端 fail-fast）
