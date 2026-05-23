# -*- coding: utf-8 -*-
"""
OA Assistant - HAR 分析入口

从 HAR 文件自动分析浏览器操作，生成 Python 接口。

使用方式:
    python analyze.py <har_file> [--site-id ID] [--site-name NAME] [--base-url URL] [--aliases A1,A2]
    python analyze.py <har_file> --mode agent   # AGENT 智能分析模式

兼容: Python 3.8+, Windows 7
"""
import argparse
import json
import os
import sys

# base_dir: 技能根目录（oa_sites/ 所在目录），所有路径基于此构建
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 确保可以导入 core 模块（scripts/ 目录下的 core/）
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from core.har_parser import parse_har
from core.boundary_detector import detect_boundaries
from core.semantic_labeler import label_operations
from core.review_presenter import present_for_review
from core.param_extractor import extract_params
from core.code_generator import generate_interface, generate_site_config
from core.diff_checker import check_diff
from core.site_matcher import match_site, list_sites
from core.interface_tester import run_tests, generate_diagnosis_report


def _get_oa_sites_dir():
    """获取站点目录（基于 base_dir）"""
    return os.path.join(BASE_DIR, 'oa_sites')


def _get_index_path():
    """获取索引文件路径"""
    return os.path.join(_get_oa_sites_dir(), '_index.json')


def analyze_har(har_path, site_id=None, site_name=None, base_url=None,
                aliases=None, confirm=False, oa_sites_dir=None,
                mode='rule', test_level='L1,L2', credentials=None):
    """
    完整的 HAR 分析流水线。

    两阶段设计：
    - 阶段1（默认）: 解析→标注→输出确认清单，等待用户确认
    - 阶段2（--confirm）: 从中间状态继续，执行参数提取→代码生成→测试

    Args:
        har_path: HAR 文件路径
        site_id: 站点标识（可选，自动从域名推断）
        site_name: 站点名称（可选）
        base_url: 基础 URL（可选，自动从 HAR 推断）
        aliases: 别名列表
        confirm: 用户已确认，继续执行代码生成
        oa_sites_dir: 站点目录
        mode: 分析模式 - 'rule'（规则模式，快速）或 'agent'（AGENT 智能分析）
        test_level: 测试级别，逗号分隔，如 'L1,L2' 或 'L1,L2,L3'
        credentials: 登录凭证 {'username': ..., 'password': ...}（L3 需要）

    Returns:
        dict: 分析结果
    """
    if oa_sites_dir is None:
        oa_sites_dir = _get_oa_sites_dir()

    index_path = os.path.join(oa_sites_dir, '_index.json')

    # ========== 步骤 1: 解析 HAR ==========
    print('\n[步骤 1/6] 解析 HAR 文件...')
    if not os.path.exists(har_path):
        print('[错误] 文件不存在: {}'.format(har_path))
        return None

    try:
        parsed = parse_har(har_path)
    except (json.JSONDecodeError, ValueError) as e:
        print('[错误] HAR 文件格式无效: {}'.format(e))
        return None
    except Exception as e:
        print('[错误] 解析 HAR 文件失败: {}'.format(e))
        return None

    if not parsed or not parsed.get('entries'):
        print('[错误] HAR 文件中没有有效的请求')
        return None

    entries = parsed['entries']
    domains = parsed.get('meta', {}).get('domains', [])
    print('  解析到 {} 个请求，涉及域名: {}'.format(len(entries), ', '.join(domains) if domains else '无'))

    # 自动推断 base_url 和 site_id
    if not base_url and domains:
        base_url = 'https://{}'.format(domains[0])

    if not site_id and domains:
        # 从域名生成 site_id: mail.company.com → mail_company
        parts = domains[0].split('.')
        site_id = parts[0] if len(parts) >= 2 else domains[0].replace('.', '_')
        site_id = site_id.replace('-', '_')

    if not site_name:
        site_name = site_id

    print('  站点: {} ({})'.format(site_name, site_id))
    print('  基础URL: {}'.format(base_url))

    # ========== 步骤 2: 检测操作边界 ==========
    print('\n[步骤 2/6] 检测操作边界...')
    groups_data = detect_boundaries(parsed)
    groups = groups_data.get('groups', [])
    write_groups = [g for g in groups if g.get('primary', {}).get('is_write')]
    print('  识别到 {} 个操作组（{} 个写操作）'.format(len(groups), len(write_groups)))

    # ========== 步骤 3: 语义标注 ==========
    print('\n[步骤 3/6] 语义标注...')
    labeled = label_operations(groups_data)
    operations = labeled.get('operations', [])
    high_conf = sum(1 for op in operations if op.get('confidence') >= 0.7)
    low_conf = len(operations) - high_conf
    print('  标注完成: {} 个操作（{} 个高置信度，{} 个低置信度）'.format(
        len(operations), high_conf, low_conf))

    # ========== AGENT 智能分析模式 ==========
    if mode == 'agent' and low_conf > 0:
        print('\n[AGENT 模式] 输出低置信度操作的结构化上下文...')
        print('  以下操作需要 AGENT 智能分析来提升识别准确度：')
        print()
        agent_data = []
        for i, op in enumerate(operations):
            if op.get('confidence', 1.0) < 0.7:
                ctx = op.get('agent_context', {})
                agent_data.append({
                    'index': i,
                    'current_label': {
                        'func_name': op['func_name'],
                        'op_type': op['op_type'],
                        'description': op['description'],
                        'confidence': op['confidence'],
                    },
                    'context': ctx,
                })
                print('  [低置信度] #{} {} {} → {}() [{}]'.format(
                    i, op['method'], op['path'],
                    op['func_name'], op['op_type']
                ))
                if ctx.get('post_body'):
                    body = ctx['post_body']
                    print('    POST body: {} keys={}'.format(
                        body.get('mime_type', ''),
                        body.get('json_keys', body.get('form_keys', []))
                    ))
                if ctx.get('response_body_keys'):
                    print('    Response keys: {}'.format(ctx['response_body_keys']))

        # 输出 JSON 供 AGENT 读取
        agent_output_path = os.path.join(
            os.path.dirname(har_path),
            'agent_analysis_{}.json'.format(site_id or 'unknown')
        )
        with open(agent_output_path, 'w', encoding='utf-8') as f:
            json.dump({
                'site_id': site_id,
                'site_name': site_name,
                'base_url': base_url,
                'low_confidence_operations': agent_data,
                'instruction': (
                    '请分析以上低置信度操作的上下文信息，为每个操作提供：'
                    '1. 正确的 op_type（login/create/list/detail/update/delete/forward/reply/approve/reject/submit/upload/download/sign/archive/transfer/publish）'
                    '2. 合适的 func_name（snake_case 格式）'
                    '3. 中文 description'
                    '返回 JSON 格式：[{"index": 0, "op_type": "...", "func_name": "...", "description": "..."}]'
                ),
            }, f, ensure_ascii=False, indent=2)
        print('\n  [OK] AGENT 分析数据已输出: {}'.format(agent_output_path))
        print('  请将 AGENT 分析结果作为 --agent-result 参数传入以继续')

        return {
            'site_id': site_id,
            'mode': 'agent',
            'agent_output_path': agent_output_path,
            'low_confidence_count': low_conf,
            'status': 'waiting_agent_input',
        }

    # ========== 步骤 4: 用户确认 ==========
    print('\n[步骤 4/6] 生成确认列表...')

    # 获取已有操作列表（用于增量对比）
    existing_operations = None
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                index_data = json.load(f)
            for site in index_data.get('sites', []):
                if site.get('id') == site_id:
                    existing_operations = site.get('operations', [])
                    break
        except Exception:
            pass

    review = present_for_review(labeled, existing_operations=existing_operations)
    print('\n' + review['summary'])
    print()

    for item in review['operations']:
        marker = '★' if item.get('is_write') else ' '
        confirmed = '✓' if item.get('confirmed', True) else '?'
        params_str = ', '.join(item.get('params', []))
        print('  {} {}. {}({}) — {} {}'.format(
            marker, item['index'], item['func_name'],
            params_str, item['method'], item['path']
        ))

    if review.get('merged'):
        print('\n  【已合并的操作】')
        for m in review['merged']:
            print('    · {} → 合并到操作 #{}'.format(m['func_name'], m['merged_into_index'] + 1))

    if review.get('diff'):
        d = review['diff']
        print('\n  【增量对比】')
        print('    新增: {}, 已存在: {}'.format(d['new_count'], d['existing_count']))

    # 保存中间状态，等待用户确认
    state_path = os.path.join(
        os.path.dirname(har_path),
        'oa_state_{}.json'.format(site_id or 'unknown')
    )
    state = {
        'har_path': har_path,
        'site_id': site_id,
        'site_name': site_name,
        'base_url': base_url,
        'aliases': aliases,
        'labeled_data': labeled,
        'oa_sites_dir': oa_sites_dir,
        'test_level': test_level,
    }
    with open(state_path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    if not confirm:
        print('\n  [等待确认] 请确认以上操作列表，确认后使用 --confirm 重新运行：')
        print('    python {} {} --site-id {} --confirm'.format(
            os.path.abspath(__file__), har_path, site_id))
        print('  中间状态已保存: {}'.format(state_path))
        return {
            'site_id': site_id,
            'site_name': site_name,
            'status': 'waiting_confirmation',
            'state_path': state_path,
            'operations': review['operations'],
            'merged': review.get('merged', []),
            'diff': review.get('diff'),
        }

    # ========== 步骤 5: 参数提取 ==========
    print('\n[步骤 5/6] 参数提取...')
    params_data = extract_params(labeled)
    ops = params_data.get('operations', [])
    for op in ops:
        var_params = [p['name'] for p in op.get('variable_params', [])]
        fixed_params = [p['name'] for p in op.get('fixed_params', [])]
        sensitive_params = [p['name'] for p in op.get('sensitive_params', [])]
        parts = []
        if var_params:
            parts.append(', '.join(var_params))
        if fixed_params:
            parts.append('固定: ' + ', '.join(fixed_params))
        if sensitive_params:
            parts.append('敏感: ' + ', '.join(sensitive_params))
        print('  {}({})'.format(op['func_name'], '; '.join(parts)))

    # ========== 步骤 6: 代码生成 ==========
    print('\n[步骤 6/6] 代码生成...')

    # 检查是否已有该站点的接口（增量更新）
    site_config_path = os.path.join(oa_sites_dir, site_id, 'site_config.json')
    diff_result = None
    if os.path.exists(site_config_path):
        print('  检测到已有接口，执行增量对比...')
        try:
            diff_result = check_diff(labeled, site_config_path)
            print('  {}'.format(diff_result['summary']))
        except Exception as e:
            print('  [警告] 增量对比失败: {}'.format(e))

    # 生成接口代码
    code = generate_interface(params_data, site_id, site_name, base_url)

    # 生成站点配置
    site_config = generate_site_config(
        params_data, site_id, site_name, base_url, aliases
    )

    # 保存文件
    site_dir = os.path.join(oa_sites_dir, site_id)
    if not os.path.exists(site_dir):
        os.makedirs(site_dir)

    interface_path = os.path.join(site_dir, 'interface.py')
    config_path = os.path.join(site_dir, 'site_config.json')

    with open(interface_path, 'w', encoding='utf-8') as f:
        f.write(code)
    print('  [OK] 接口代码: {}'.format(interface_path))

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(site_config, f, ensure_ascii=False, indent=2)
    print('  [OK] 站点配置: {}'.format(config_path))

    # 更新索引
    _update_index(index_path, site_id, site_name, base_url, aliases,
                  params_data, site_dir)

    # 输出使用示例
    print('\n' + '=' * 50)
    print('生成完成！使用示例:')
    print('=' * 50)
    class_name = ''.join(
        word.capitalize() for word in site_id.replace('-', '_').split('_')
    )
    print()
    print('  from oa_sites.{}.interface import {}'.format(site_id, class_name))
    print()
    print('  oa = {}()'.format(class_name))
    print('  oa.login("username", "password")')
    if ops:
        # 找一个非 login 的操作作为示例
        for op in ops:
            if op['op_type'] != 'login':
                var_params = [p['name'] for p in op.get('variable_params', [])]
                args = ', '.join(var_params[:2])  # 最多显示2个参数
                if args:
                    args = '"' + args.replace(', ', '", "') + '"'
                    # 对数字参数不加引号
                    args_parts = []
                    for p in op.get('variable_params', [])[:2]:
                        if p.get('python_type') == 'int':
                            args_parts.append('1')
                        else:
                            args_parts.append('"{}"'.format(p['name']))
                    args = ', '.join(args_parts)
                print('  result = oa.{}({})'.format(op['func_name'], args))
                break
    print()

    # ========== 步骤 7: 自动测试 ==========
    test_result = None
    if test_level:
        levels = [l.strip() for l in test_level.split(',') if l.strip()]
        print('\n[步骤 7] 自动测试 ({})...'.format(','.join(levels)))
        test_result = run_tests(
            interface_path=interface_path,
            site_config_path=config_path,
            site_id=site_id,
            base_url=base_url,
            credentials=credentials,
            levels=levels,
            base_dir=BASE_DIR,
        )

        if not test_result['overall_passed']:
            # 生成诊断报告
            report_path = os.path.join(site_dir, 'diagnosis_report.json')
            generate_diagnosis_report(test_result, report_path)
            print('\n  [!] 测试未通过，诊断报告: {}'.format(report_path))
            print('  修复后重新运行测试:')
            print('    python <base_dir>/core/interface_tester.py {} \\'.format(interface_path))
            print('      --site-config {} --site-id {} --levels {}'.format(
                config_path, site_id, ','.join(levels)))
        else:
            print('\n  [OK] 所有测试通过！')

    return {
        'site_id': site_id,
        'site_name': site_name,
        'interface_path': interface_path,
        'config_path': config_path,
        'operations_count': len(ops),
        'diff': diff_result,
        'test_result': test_result,
    }


def _update_index(index_path, site_id, site_name, base_url, aliases,
                  params_data, site_dir):
    """更新知识库索引"""
    # 读取现有索引
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)
    else:
        index = {'version': '1.0', 'sites': []}

    # 去重操作列表（与 generate_site_config 一致）
    operations = []
    seen_keys = {}
    for op in params_data.get('operations', []):
        op_type = op.get('op_type', '')
        resource_name = op.get('resource_name', '')
        key = op_type if op_type in ('login', 'logout') else (op_type, resource_name)
        if key in seen_keys:
            if op.get('is_write') and not seen_keys[key].get('is_write'):
                operations = [o for o in operations if o['name'] != seen_keys[key]['func_name']]
            else:
                continue
        seen_keys[key] = op
        variable_params = [p['name'] for p in op.get('variable_params', [])]
        operations.append({
            'name': op['func_name'],
            'params': variable_params,
        })

    # 更新或添加站点
    site_entry = {
        'id': site_id,
        'name': site_name,
        'aliases': aliases or [site_name],
        'description': '{}接口'.format(site_name),
        'base_urls': [base_url] if base_url else [],
        'operations': operations,
        'path': os.path.relpath(site_dir, os.path.dirname(index_path)).replace('\\', '/') + '/',
    }

    # 替换或追加
    found = False
    for i, s in enumerate(index['sites']):
        if s.get('id') == site_id:
            index['sites'][i] = site_entry
            found = True
            break
    if not found:
        index['sites'].append(site_entry)

    # 写入
    import datetime
    index['last_updated'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print('  [OK] 索引更新: {}'.format(index_path))


def main():
    parser = argparse.ArgumentParser(
        description='OA Assistant - 从 HAR 文件自动生成 Python 接口'
    )
    parser.add_argument('har_file', help='HAR 文件路径')
    parser.add_argument('--site-id', help='站点标识（默认从域名推断）')
    parser.add_argument('--site-name', help='站点名称')
    parser.add_argument('--base-url', help='基础 URL（默认从 HAR 推断）')
    parser.add_argument('--aliases', help='站点别名，逗号分隔')
    parser.add_argument('--confirm', action='store_true',
                        help='确认操作列表，继续执行代码生成')
    parser.add_argument('--oa-sites-dir', help='站点目录')
    parser.add_argument('--mode', choices=['rule', 'agent'], default='rule',
                        help='分析模式: rule=规则模式(快速), agent=AGENT智能分析')
    parser.add_argument('--agent-result', help='AGENT 分析结果 JSON 文件路径')
    parser.add_argument('--test', dest='test_level', default='L1,L2',
                        help='测试级别: L1,L2,L3 (默认 L1,L2; 空字符串跳过测试)')
    parser.add_argument('--username', help='登录用户名（L3 测试需要）')
    parser.add_argument('--password', help='登录密码（L3 测试需要）')

    args = parser.parse_args()

    aliases = None
    if args.aliases:
        aliases = [a.strip() for a in args.aliases.split(',') if a.strip()]

    credentials = None
    if args.username and args.password:
        credentials = {'username': args.username, 'password': args.password}

    result = analyze_har(
        har_path=args.har_file,
        site_id=args.site_id,
        site_name=args.site_name,
        base_url=args.base_url,
        aliases=aliases,
        confirm=args.confirm,
        oa_sites_dir=args.oa_sites_dir,
        mode=args.mode,
        test_level=args.test_level,
        credentials=credentials,
    )

    if result is None:
        sys.exit(1)

    # 如果是 AGENT 模式且需要回填结果
    if result.get('status') == 'waiting_agent_input' and args.agent_result:
        _apply_agent_result(args.agent_result, result.get('site_id'))


def _apply_agent_result(agent_result_path, site_id):
    """
    将 AGENT 分析结果回填到标注数据中。

    AGENT 分析结果格式:
    [
        {"index": 0, "op_type": "submit", "func_name": "submit_report", "description": "提交报告"},
        ...
    ]
    """
    if not os.path.exists(agent_result_path):
        print('[错误] AGENT 结果文件不存在: {}'.format(agent_result_path))
        return

    with open(agent_result_path, 'r', encoding='utf-8') as f:
        agent_results = json.load(f)

    print('\n[AGENT 回填] 应用 AGENT 分析结果...')
    print('  收到 {} 个操作的修正'.format(len(agent_results)))

    for item in agent_results:
        print('  #{}: {} → {}() [{}]'.format(
            item.get('index', '?'),
            item.get('op_type', '?'),
            item.get('func_name', '?'),
            item.get('description', '?')
        ))

    print('\n  请使用 --mode rule 重新运行以应用修正')


if __name__ == '__main__':
    main()
