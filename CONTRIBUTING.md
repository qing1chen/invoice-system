# 贡献指南 · Contributing Guide

感谢你对 Invoice System 的兴趣!以下是参与贡献的流程。

## 🐛 报告 Bug

在提交 Issue 前请先搜索是否已有相同问题。新 Issue 请尽量包含:

- 复现步骤
- 期望行为 vs 实际行为
- 环境信息(OS / Python 版本 / Node 版本 / 浏览器)
- 相关日志或截图

## 💡 功能建议

欢迎通过 Issue 提出新功能建议。请描述:

- 解决了什么问题
- 提议的实现方式
- 是否涉及后端 (`invoice-toolkit/`) 还是前端 (`frontend/`)
- 是否愿意自己实现

## 🔧 代码贡献

### 开发环境

```bash
git clone https://github.com/<your-username>/invoice-system.git
cd invoice-system

# ─── 后端 ─────────────────────────
cd invoice-toolkit
conda create -n invoice-dev python=3.10 -y
conda activate invoice-dev
pip install -r requirements.txt
cd ..

# ─── 前端 ─────────────────────────
cd frontend
npm install
cd ..
```

### 提交流程

1. Fork 本仓库
2. 创建特性分支: `git checkout -b feature/your-feature`
3. 编写代码 + 测试
4. 运行格式化与检查:
   ```bash
   # Python
   cd invoice-toolkit
   ruff check .
   ruff format .
   pytest

   # 前端
   cd frontend
   npm run lint
   ```
5. 提交改动(遵循 [Conventional Commits](https://www.conventionalcommits.org/)):
   - `feat(backend): 新增 xxx 功能`
   - `fix(frontend): 修复 xxx bug`
   - `docs: 更新 README`
   - `refactor(backend): 重构 classifier`
6. 推送分支并开启 PR

### 代码规范

- **Python**: 遵循 PEP 8,使用 `ruff` 进行检查与格式化
- **JavaScript/React**: 遵循 ESLint 默认规则
- **提交信息**: 使用 Conventional Commits 格式,推荐带 `(backend)` / `(frontend)` scope
- **文档字符串**: 公共 API 必须有 docstring

### PR Checklist

- [ ] 代码通过 lint 与测试
- [ ] 新增功能有对应的文档更新
- [ ] 相关的 Issue 已在 PR 描述中引用
- [ ] 对 breaking changes 已在描述中说明
- [ ] 如修改了后端 API,前端对应调用已同步更新

## 🌏 翻译

欢迎贡献多语言支持。目前支持中文与英文,如需添加其他语言,请新建 `README.<lang>.md`。

---

再次感谢你的贡献!💖
