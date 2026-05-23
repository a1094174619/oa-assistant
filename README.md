# OA Assistant - 办公自动化助手

自动学习企业内部 OA 系统并生成 Python 接口，一次学习永久可用。

## 概述

OA Assistant 通过分析 Chrome 浏览器导出的 HAR（HTTP Archive）文件，自动识别企业内部 OA 系统的 API 操作（发送邮件、审批、查询、发文等），并生成可直接调用的 Python 接口代码。生成的接口附带三级闭环测试框架，确保代码可用性。

### 核心特性

- **HAR 自动分析**：从 Chrome HAR 文件中自动提取 API 操作，识别操作语义（CRUD、审批、转发、回复等）
- **Python 代码生成**：自动生成 `interface.py`，包含完整的 HTTP 请求封装、Cookie 管理、密码加密钩子
- **增量学习**：支持对已知系统补充新操作，智能对比已有操作，只合并新增部分
- **双模式分析**：规则模式（快速）和 AGENT 智能分析模式（高准确率）
- **三级闭环测试**：L1 静态检查 → L2 Dry-Run → L3 真实测试，测试失败自动生成诊断报告
- **宽兼容**：Python 3.8+，Windows 7+，无重依赖

## 快速开始

### 安装

```bash
# 克隆仓库
git clone https://github.com/a1094174619/oa-assistant.git
cd oa-assistant

# 安装依赖
pip install requests
```

### 基础用法

```bash
# 1. 导出 HAR 文件（在 Chrome 中操作）
#   - F12 → Network → 勾选 Preserve log → 清空 → 执行操作 → 右键 "Save all as HAR with content"

# 2. 分析 HAR 文件（阶段1：识别操作）
python scripts/analyze.py path/to/export.har --site-id my_oa --site-name "我的OA系统"

# 3. 确认后生成代码（阶段2：生成接口）
python scripts/analyze.py path/to/export.har --site-id my_oa --confirm

# 4. 直接使用生成的接口
python oa_sites/my_oa/interface.py --mode dry-run
```

### 使用生成的接口

```python
import sys
sys.path.insert(0, 'oa_sites')
from my_oa.interface import MyOAInterface

api = MyOAInterface(base_url='https://oa.company.com')
api.login('username', 'password')
result = api.send_mail(to='user@company.com', subject='Hello', body='...')
print(result)
```

## 工作流程

```
用户意图 → 读取 _index.json 知识库
   ├── 系统+操作已存在 → 直接加载 interface.py 执行
   ├── 系统存在但操作缺失 → 增量学习（导出 HAR → 分析 → 合并）
   └── 系统不存在 → 完整学习（导出 HAR → 分析 → 生成代码）
```

### 分析流水线

| 步骤 | 说明 |
|------|------|
| 步骤 1 | 解析 HAR 文件，过滤静态资源，提取有效 HTTP 请求 |
| 步骤 2 | 检测操作边界，将连续请求归组为操作 |
| 步骤 3 | 语义标注，识别操作类型（创建/查询/审批/删除等） |
| 步骤 4 | 展示确认清单，等待用户确认 |
| 步骤 5 | 参数提取与代码生成 |
| 步骤 6 | 运行 L1+L2 测试 |

## 项目结构

```
oa-assistant/
├── SKILL.md                    # 技能定义文件
├── README.md                   # 本文件
├── scripts/
│   ├── analyze.py              # 主入口：HAR 分析流水线
│   └── core/
│       ├── har_parser.py       # HAR 文件解析器
│       ├── boundary_detector.py # 操作边界检测
│       ├── semantic_labeler.py  # 语义标注
│       ├── review_presenter.py  # 确认清单展示
│       ├── param_extractor.py   # 参数提取
│       ├── code_generator.py    # Python 接口代码生成
│       ├── diff_checker.py      # 增量对比
│       ├── interface_tester.py  # 三级闭环测试框架
│       └── site_matcher.py      # 站点匹配与路由
├── oa_sites/
│   ├── _base.py                # 接口基类（HTTP/会话/重试/加密钩子）
│   ├── _index.json             # 站点索引（知识库）
│   └── <site_id>/              # 各站点生成的接口目录
│       ├── interface.py        # 自动生成的接口代码
│       └── site_config.json    # 站点配置
└── assets/
    └── interface.py.j2         # Jinja2 代码模板（可选，高级定制用）
```

## 测试框架

生成接口后自动运行 L1+L2 测试：

| 级别 | 名称 | 成本 | 检查内容 |
|------|------|------|----------|
| L1 | 静态检查 | 零 | 语法、导入、类定义、方法签名、配置一致性 |
| L2 | Dry-Run | 零网络 | 实例化、请求构造、URL 拼接、参数传递 |
| L3 | Live-Test | 需凭证 | 真实请求发送、响应验证、登录认证 |

测试失败时自动生成 `diagnosis_report.json` 诊断报告，包含错误类型和修复建议。

```bash
# L3 真实测试（需要登录凭证）
python scripts/analyze.py path/to/export.har --site-id my_oa --confirm \
    --test L1,L2,L3 --username user --password pass

# 独立测试已有接口
python scripts/core/interface_tester.py oa_sites/my_oa/interface.py \
    --site-config oa_sites/my_oa/site_config.json \
    --site-id my_oa --levels L1,L2
```

## AGENT 智能分析模式

当规则模式对非标准 URL 识别置信度低时，可启用 AGENT 模式：

```bash
# Step 1: 输出低置信度操作供 AGENT 分析
python scripts/analyze.py path/to/export.har --mode agent --site-id my_oa

# Step 2: AGENT 分析输出的 JSON，返回修正结果

# Step 3: 回填修正结果继续
python scripts/analyze.py path/to/export.har --mode agent --agent-result result.json
```

## 环境要求

- Python 3.8+
- requests
- 兼容 Windows 7+

## License

MIT
