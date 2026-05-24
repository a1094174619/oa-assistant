---
name: "oa-assistant"
description: "Learn internal enterprise OA systems from Chrome HAR exports and automate operations via generated Python interfaces. Invoke when the user asks to perform any work-related action: (1) Sending emails, (2) Submitting forms or approvals, (3) Querying records or reports, (4) Filing documents, (5) Any internal web system operation. Checks oa_sites knowledge base first; if unknown system or operation, guides user to export HAR for learning."
---

# OA Assistant - 办公自动化助手

自动学习企业内部 OA 系统并生成 Python 接口，一次学习永久可用。

## 触发后第一步：路由匹配

无论用户说什么，**第一步永远是读取 `oa_sites/_index.json`**，用用户意图在 `aliases` 和 `operations` 中匹配。

```
用户意图 → 读取 _index.json → 匹配 aliases/operations
```

## 三种分支

| 系统匹配 | 操作匹配 | 分支 | 行为 |
|---------|---------|------|------|
| ✅ 找到 | ✅ 找到 | **调用模式** | 加载 interface.py → 执行 |
| ✅ 找到 | ❌ 未找到 | **增量学习** | 引导导出 HAR → 补充操作 |
| ❌ 未找到 | - | **完整学习** | 引导导出 HAR → 学习新系统 |

---

## 调用模式

当 oa_sites 目录中已有匹配的站点和操作时：

1. 读取 `oa_sites/<site_id>/site_config.json` 获取配置
2. 读取 `oa_sites/<site_id>/interface.py` 获取接口代码
3. 在 Python 中动态加载并实例化接口类
4. 调用 `login()` 方法登录（凭证从用户处获取或配置文件读取）
5. 调用对应的业务方法
6. 返回结果给用户

### 执行方式

```bash
python <base_dir>/oa_sites/<site_id>/interface.py
```

或动态加载：

```python
import sys
sys.path.insert(0, '<base_dir>/oa_sites')
from <site_id>.interface import <ClassName>
api = <ClassName>()
api.login('<username>', '<password>')
result = api.<method_name>(<params>)
```

---

## 学习模式

当 oa_sites 目录中没有匹配的站点或操作时：

### Step 1: 引导用户导出 HAR

告知用户：

> 我需要先学习这个系统，请按以下步骤导出 HAR 文件：
> 1. 打开 Chrome，按 F12 打开开发者工具
> 2. 切换到 **Network** 标签
> 3. 勾选 **Preserve log**（保留日志）
> 4. ⚠️ **不要勾选任何过滤选项**（XHR/JS/CSS 等），保持"All"全量导出
> 5. 点击清除按钮 🔴 清空现有日志
> 6. 在网页上完成完整的操作流程（如：登录 → 发邮件 → 转发 → 回复）
> 7. 右键点击任意请求 → 选择 **"Save all as HAR with content"**
> 8. 保存 .har 文件，把文件路径告诉我

> 注意：必须全量导出，前端加密逻辑通常在 JS 文件中，后续可能需要分析。

### Step 2: 运行分析流水线

收到 HAR 文件后，运行分析：

```bash
# 阶段1: 分析 HAR，输出确认清单（不会生成代码）
python <base_dir>/scripts/analyze.py <har_file> --site-id <site_id> --site-name "<站点名称>"

# AGENT 智能分析模式（低置信度操作输出供 AGENT 分析）
python <base_dir>/scripts/analyze.py <har_file> --mode agent --site-id <site_id> --site-name "<站点名称>"
```

### Step 3: 用户确认（阻断点）

**必须将分析结果展示给用户确认后，才能进入代码生成。** 阶段1运行后会输出操作清单并中断，不会生成任何代码。

展示格式：
- 每个操作：编号、HTTP 方法、URL 路径、建议函数名、参数列表
- 被合并的操作：注明合并原因
- 低置信度操作：特别标注 ⚠

用户确认选项：
| 用户回复 | 行为 |
|---------|------|
| "确认" / "没问题" / "开始生成" | 阶段2：加 `--confirm` 继续生成代码 |
| "第N个改成 xxx" | 修改后重新展示 |
| "第M和第N合并" | 合并后重新展示 |
| "取消" | 退出，不生成代码 |

### Step 4: 代码生成

用户确认后，加 `--confirm` 重新运行：

```bash
# 阶段2: 用户确认后，继续生成代码
python <base_dir>/scripts/analyze.py <har_file> --site-id <site_id> --confirm
```

生成结果：
- `oa_sites/<site_id>/interface.py` — Python 接口代码
- `oa_sites/<site_id>/site_config.json` — 站点配置
- `oa_sites/_index.json` — 自动更新索引

### Step 5: 立即执行

代码生成后，立即加载新接口执行用户原本的任务。

---

## 闭环测试机制

生成的接口代码几乎不可能一次就完美运行，因此内置了三级闭环测试框架。

### 测试级别

| 级别 | 名称 | 成本 | 检查内容 |
|------|------|------|----------|
| L1 | 静态检查 | 零 | 语法、导入、类定义、方法签名、配置一致性 |
| L2 | Dry-Run | 零网络 | 实例化、请求构造、URL拼接、参数传递 |
| L3 | Live-Test | 需凭证 | 真实请求、响应验证、登录认证 |

### 自动测试

代码生成后自动运行 L1+L2 测试（`--confirm` 阶段自动执行）：

```bash
# 跳过测试
python <base_dir>/scripts/analyze.py <har_file> --site-id <site_id> --confirm --test ""

# 运行 L3 真实测试（需要凭证）
python <base_dir>/scripts/analyze.py <har_file> --site-id <site_id> --confirm \
    --test L1,L2,L3 --username <user> --password <pass>
```

### 独立测试器

```bash
# L1+L2 测试
python <base_dir>/scripts/core/interface_tester.py <base_dir>/oa_sites/<site_id>/interface.py \
    --site-config <base_dir>/oa_sites/<site_id>/site_config.json \
    --site-id <site_id> --levels L1,L2

# L3 真实测试
python <base_dir>/scripts/core/interface_tester.py <base_dir>/oa_sites/<site_id>/interface.py \
    --site-id <site_id> --base-url <url> \
    --username <user> --password <pass> --levels L1,L2,L3

# 生成诊断报告
python <base_dir>/scripts/core/interface_tester.py <base_dir>/oa_sites/<site_id>/interface.py \
    --site-id <site_id> --levels L1,L2 --report <output_dir>/diagnosis_report.json
```

### 内置自测

生成的 interface.py 自带 `if __name__ == '__main__'` 自测代码：

```bash
# Dry-Run 模式（不发送请求）
python <base_dir>/oa_sites/<site_id>/interface.py --mode dry-run

# Live 模式（真实发送）
python <base_dir>/oa_sites/<site_id>/interface.py --mode live \
    --base-url <url> --username <user> --password <pass>
```

### 诊断报告

测试失败时自动生成 `diagnosis_report.json`，包含：
- 错误类型和消息
- 修复建议（fix_hint）
- 捕获的请求详情（L2）
- 响应样本（L3）

诊断报告可供 AGENT 分析后自动修复，形成闭环：

```
生成代码 → 测试 → 诊断报告 → AGENT 修复 → 重新测试 → 通过
```

---

## 增量学习

当已有系统需要补充新操作时：

1. 引导用户导出包含新操作的 HAR
2. 运行分析流水线到 Step 3
3. 使用 `diff_checker.py` 对比已有操作：

```bash
python <base_dir>/scripts/core/diff_checker.py <output_dir>/oa_labeled.json \
    --config <base_dir>/oa_sites/<site_id>/site_config.json
```

4. 只展示新增/变更的操作，用户确认后合并到现有 interface.py

---

## 双模式分析架构

### 规则模式（默认，快速）

基于预定义的语义映射表和启发式规则进行操作识别。
- 优点：速度快，不消耗 TOKEN
- 缺点：对非标准 URL 路径识别率较低

### AGENT 智能分析模式

当规则模式识别置信度低时，输出结构化上下文供 AGENT 分析。

```bash
# Step 1: 运行 AGENT 模式，输出低置信度操作
python <base_dir>/scripts/analyze.py <har_file> --mode agent --site-id <site_id>

# Step 2: AGENT 分析输出的 JSON 文件，返回修正结果

# Step 3: 回填修正结果
python <base_dir>/scripts/analyze.py <har_file> --mode agent --agent-result <result.json>
```

AGENT 分析数据包含：
- 完整的 URL、路径、HTTP 方法
- POST body 结构（字段名，不含值）
- 响应结构（字段名，不含值）
- Header 来源追踪
- 当前规则模式的标注结果

AGENT 返回格式：
```json
[
  {"index": 0, "op_type": "submit", "func_name": "submit_report", "description": "提交报告"}
]
```

---

## 凭证管理

生成的接口类继承自 `BaseOAInterface`，支持两种凭证提供方式：

### 方式1：函数参数传入

```python
api = MailOA(base_url="https://mail.company.com")
api.login(username="zhangsan", password="xxx")
```

### 方式2：凭证文件自动加载（推荐固定账号场景）

每个站点的凭证存储在 `oa_sites/<site_id>/credentials.json`，与接口代码同目录，`login()` 无参数时自动读取：

```bash
# 写入凭证文件（site_id 对应 oa_sites 目录下的站点标识）
echo '{"username": "zhangsan", "password": "xxx"}' > oa_sites/mail_oa/credentials.json
```

```python
api = MailOA()  # base_url 已有默认值
api.login()     # 自动从 oa_sites/mail_oa/credentials.json 读取凭证
```

也可以混合使用——传入的参数优先：

```python
api.login(username="lisi")  # username 用传入的，password 从凭证文件读取
```

### 凭证文件格式

基础格式（仅账号密码）：
```json
{
  "username": "xxx",
  "password": "xxx"
}
```

扩展格式（含额外登录参数，如公司ID、域名等）：
```json
{
  "username": "xxx",
  "password": "xxx",
  "company_id": "acme",
  "domain": "hr.acme.com"
}
```

`credentials.json` 中的所有字段都会被 `login()` 自动读取并作为请求参数发送。字段名与 HAR 中捕获的实际参数名一致。

### 前端密码加密

大部分 OA 系统在登录时会对密码做前端加密（MD5/SHA/RSA/AES/SM2），直接发送明文密码会导致登录失败。

**加密检测**：HAR 解析时自动检测密码字段是否被加密，并在分析结果中提示。

**加密钩子**：所有生成的接口类包含 `_encrypt_password()` 方法，`login()` 会自动调用它加密密码类字段。默认不加密，需根据目标系统实现。

```python
# 示例：MD5 加密
import hashlib
def _encrypt_password(self, password):
    return hashlib.md5(password.encode()).hexdigest()
```

生成后 L3 测试如果登录失败，从诊断报告中确认是否为加密问题，然后：
1. 打开浏览器 F12 定位登录请求中的密码值
2. 搜索前端 JS 中的加密逻辑（搜索 `encrypt`/`md5`/`sha`/`rsa`/`sm2`）
3. 将加密逻辑实现到 `interface.py` 的 `_encrypt_password()` 方法中
4. 重新运行 L3 测试

### Cookie 兜底登录

当密码加密过于复杂无法逆向时，可以直接从浏览器复制 Cookie 作为兜底登录方式。`login()` 会优先检查凭证中是否有 `cookies` 字段，有则直接加载跳过登录。

credentials.json 格式（dict 格式）：
```json
{
  "cookies": {
    "session_id": "abc123",
    "token": "eyJhbGci..."
  }
}
```

或字符串格式（从浏览器直接复制）：
```json
{
  "cookies": "session_id=abc123; token=eyJhbGci..."
}
```

> 注意：Cookie 有有效期，过期后需重新从浏览器复制。建议在凭证文件中同时保留 username/password，Cookie 过期后可尝试密码登录。

### 站点目录结构

```
oa_sites/
├── _index.json
├── _base.py
├── mail_oa/
│   ├── interface.py          ← 接口代码
│   ├── site_config.json      ← 站点配置
│   └── credentials.json      ← 凭证（独立存储）
├── budget/
│   ├── interface.py
│   ├── site_config.json
│   └── credentials.json
└── ...
```

> 注意：credentials.json 包含敏感信息，建议在 .gitignore 中排除 `**/credentials.json`。

---

## 接口调用失败处理

1. 首次失败 → 自动重试（最多 2 次）
2. 重试仍失败 → 检查是否登录过期，尝试重新登录
3. 重新登录后仍失败 → 告知用户，询问是否需要重新学习该接口

---

## 站点管理

### 查看已学习系统

```bash
python <base_dir>/scripts/core/site_matcher.py --list
```

### 删除站点

删除站点目录并同步清理 `_index.json` 中的条目：

```bash
python <base_dir>/scripts/core/site_matcher.py --delete <site_id>
```

### 匹配测试

```bash
python <base_dir>/scripts/core/site_matcher.py "发邮件"
python <base_dir>/scripts/core/site_matcher.py "审批报销"
```

---

## 文件上传与下载

生成的接口支持文件上传和下载操作。HAR 解析时会自动识别 `multipart/form-data` 中的文件 part 和二进制下载响应，生成的代码会使用基类的 `_upload()` / `_download()` 方法。

### 文件上传

当 HAR 中包含 `multipart/form-data` 请求时，自动识别文件字段并生成上传方法：

```python
# 单文件上传
result = api.upload_attachment(file='/path/to/file.pdf')

# 带额外表单字段的上传
result = api.upload_report(file='/path/to/report.xlsx',
                           report_name='月度报告',
                           report_type='monthly')
```

底层调用基类的 `_upload()` 方法：

```python
# 手动调用（自定义场景）
result = api._upload('/api/file/upload',
                     file_paths='/path/to/file.pdf',
                     field_name='file',
                     extra_fields={'category': 'report'})

# 多文件上传（同一字段）
result = api._upload('/api/file/batch',
                     file_paths=['/path/a.pdf', '/path/b.docx'],
                     field_name='files')

# 多字段文件上传
result = api._upload('/api/file/multi',
                     file_paths={'attachment': '/path/a.pdf',
                                 'signature': '/path/sign.png'})
```

### 文件下载

当 HAR 中响应为二进制文件（`Content-Disposition: attachment` 或二进制 MIME 类型）时，自动生成下载方法：

```python
# 下载到指定路径
result = api.download_report(report_id='123', save_path='/path/to/save/')

# 不保存，仅获取内容
result = api.download_report(report_id='123')
content = result['content']   # bytes
filename = result['filename'] # 推断的文件名
```

底层调用基类的 `_download()` 方法：

```python
# 手动调用（自定义场景）
result = api._download('/api/file/123', save_path='./downloads/')
# 返回: {'content': bytes, 'filename': 'report.pdf', 'size': 12345, 'saved_path': '...'}
```

### `_request()` 的二进制响应处理

普通 `_request()` 调用遇到二进制响应时（如意外返回文件），不会尝试 JSON 解析，而是返回：

```python
{
    'status_code': 200,
    'content': b'...',           # 原始 bytes
    'mime_type': 'application/pdf',
    'content_disposition': 'attachment; filename="report.pdf"',
    'size': 12345,
}
```

---

## 注意事项
- 不依赖 Jinja2（内置模板引擎）
- HAR 文件可能很大，分析时注意内存
- 部分系统使用 iframe，HAR 中可能无法完整捕获，需注意处理