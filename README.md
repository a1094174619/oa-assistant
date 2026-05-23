# OA Assistant — 让你的 AI 替你搞定那堆垃圾 OA

> 别再手动点那堆反人类的 OA 流程了。导出一个 HAR 文件，剩下的交给 AI。

## 这玩意儿是干嘛的

每个公司都有那么几个祖传 OA 系统——发个邮件要点 8 下，走个审批要刷新 3 次页面，查个报表要填 5 个筛选条件然后弹窗告诉你"当前无数据"。

更恶心的是，这些破系统你还必须天天用。

**OA Assistant 解决的就是这个问题**：你在 Chrome 里把操作流程走一遍，导出 HAR 文件丢给本工具，它自动帮你生成本地 Python 接口。从此以后，你写个脚本就能替你干活，或者直接让 AI Agent 去操作这些 OA 系统。

### 核心能力

- **一个 HAR 文件走天下**：导出一次，自动识别所有 API 操作（发邮件、审批、查询、发文、上传……）
- **零手动编码**：自动生成 `interface.py`，HTTP 封装、Cookie 管理、加密钩子全给你写好
- **三级自检**：生成的代码先自我测试（语法→请求构造→真实发送），挂了还能自诊断
- **一次学习永久复用**：学过的系统存在 oa_sites 目录下，下次直接调用
- **AI 友好**：就是为 Agent 设计的，让你的 AI 能直接操作企业内部系统

## 快速开始

```bash
git clone https://github.com/a1094174619/oa-assistant.git
cd oa-assistant
pip install requests
```

### 三步搞定

```bash
# 1. 在 Chrome 里操作一遍你的OA流程
#    F12 → Network → 勾 Preserve log → 清空 → 走完流程 → 右键 Save all as HAR with content

# 2. 丢给分析器，它自己识别出所有操作
python scripts/analyze.py 你导出的.har --site-id my_shit_oa --site-name "XX垃圾OA"

# 3. 确认无误，生成接口代码
python scripts/analyze.py 你导出的.har --site-id my_shit_oa --confirm
```

生成完你就能直接调用了：

```python
import sys
sys.path.insert(0, 'oa_sites')
from my_shit_oa.interface import MyShitOAInterface

api = MyShitOAInterface(base_url='https://oa.company.com')
api.login('你的账号', '你的密码')
api.send_mail(to='boss@company.com', subject='辞职信', body='我不干了')

# 批量审批？脚本循环一下就行
for item in api.get_pending_approvals():
    api.approve(item['id'])
```

## 这玩意儿怎么工作的

```
用户说"帮我发个邮件"
         │
         ▼
    查站点索引 _index.json
    ┌────────┼────────┐
    ▼        ▼        ▼
 已知系统   已知系统   啥都不知道
 已知操作   未知操作   ▼
    │        │      导出HAR
    ▼        ▼        │
 直接调用  增量学习   ▼
           (补充操作) 完整学习
                    自动识别→生成接口
```

### 分析流水线（自动执行）

你的 HAR 文件进去，Python 接口出来：

| 步骤 | 干的事 |
|------|--------|
| ① 解析 | 过滤掉 CSS/JS/图片这些垃圾，只留 API 请求 |
| ② 归组 | 把同一个操作的连续请求绑在一起 |
| ③ 标注 | 识别这是"创建"还是"查询"还是"审批" |
| ④ 确认 | 列出所有识别结果等你过目（不满意可以改） |
| ⑤ 生成 | 输出完整的 Python 接口类 |
| ⑥ 自测 | L1 语法检查 + L2 请求模拟，挂了出诊断报告 |

## 项目结构

```
oa-assistant/
├── SKILL.md                    # AI Agent 技能定义文件
├── README.md                   # 你现在在看的东西
├── scripts/
│   ├── analyze.py              # 主入口，一行命令走完整个流水线
│   └── core/
│       ├── har_parser.py       # HAR 解析，自动过滤静态资源
│       ├── boundary_detector.py # 操作边界检测
│       ├── semantic_labeler.py  # 语义标注（增删改查审批转发…）
│       ├── review_presenter.py  # 确认清单
│       ├── param_extractor.py   # 参数提取
│       ├── code_generator.py    # Python 代码生成（不依赖 Jinja2）
│       ├── diff_checker.py      # 增量对比，只合并新操作
│       ├── interface_tester.py  # 三级闭环测试 + 诊断报告
│       └── site_matcher.py      # 站点索引路由匹配
├── oa_sites/
│   ├── _base.py                # 基类（重试/超时/Cookie/加密钩子）
│   ├── _index.json             # 全系统索引
│   └── <site_id>/              # 每个学过的系统一个目录
│       ├── interface.py        # ★ 自动生成的接口 ★
│       └── site_config.json    # 站点配置
└── assets/
    └── interface.py.j2         # Jinja2 模板（高级定制用）
```

## 测试——生成的代码不是黑盒

没有人能一次写对代码，机器也不行。所以每个生成的接口都自带三级测试：

| 级别 | 叫什么 | 代价 | 测什么 |
|------|--------|------|--------|
| L1 | 静态检查 | 零 | 语法对不对，导入有没有问题，方法签名完整吗 |
| L2 | Dry-Run | 零网络 | 类能不能实例化，URL 拼接对不对，参数传得通吗 |
| L3 | Live-Test | 要登录 | 真发请求，返回结构对不对，登录状态正常吗 |

不通过就自动出 `diagnosis_report.json`，告诉你哪儿挂了、建议怎么修。然后让 AI 读报告自动修，修完再测，形成一个闭环：

```
生成代码 → 自测 → 挂了 → 诊断报告 → AI 修复 → 再测 → 通过 ✅
```

```bash
# L3 真枪实弹测试
python scripts/analyze.py xxx.har --site-id my_oa --confirm \
    --test L1,L2,L3 --username 你的账号 --password 你的密码

# 单独测试已有接口
python scripts/core/interface_tester.py oa_sites/my_oa/interface.py \
    --site-config oa_sites/my_oa/site_config.json --site-id my_oa
```

## 环境要求

- Python 3.8+
- `pip install requests`
- Windows 7 都能跑（够接地气吧）

## License

MIT — 拿去用，帮你从 OA 地狱里解脱出来。
