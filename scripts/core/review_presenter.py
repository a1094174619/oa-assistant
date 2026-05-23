# -*- coding: utf-8 -*-
"""
确认清单生成器

将语义标注后的操作列表整理成用户可读的确认清单，
供用户在代码生成前审核和修正。

兼容: Python 3.8+, Windows 7
"""
import json
import argparse
from typing import Dict, List, Optional, Any


def present_for_review(labeled_data, existing_operations=None):
    """
    生成用户确认清单。

    Args:
        labeled_data: semantic_labeler.label_operations() 的输出
        existing_operations: 已有操作列表（增量学习时使用）

    Returns:
        dict: {
            'summary': str,
            'operations': [ 确认项列表 ],
            'merged': [ 被合并的操作说明 ],
            'diff': { 增量对比结果 } or None,
            'suggestions': [ 建议列表 ]
        }
    """
    operations = labeled_data.get('operations', [])
    review_items = []
    merged_items = []
    suggestions = []

    # 增量对比
    diff = None
    if existing_operations:
        diff = _compute_diff(operations, existing_operations)

    # 检测可合并的操作（如翻页合并到列表）
    merge_map = _detect_mergeable_operations(operations)

    for i, op in enumerate(operations):
        # 跳过被合并的操作
        if i in merge_map:
            target = merge_map[i]
            merged_items.append({
                'operation_index': i,
                'func_name': op['func_name'],
                'reason': '已合并到 {}'.format(operations[target]['func_name']),
                'merged_into_index': target,
            })
            continue

        # 构建确认项
        item = {
            'index': i + 1,
            'op_type': op['op_type'],
            'func_name': op['func_name'],
            'method': op['method'],
            'path': op['path'],
            'description': op['description'],
            'confidence': op['confidence'],
            'confidence_level': _confidence_level(op['confidence']),
            'is_write': op['is_write'],
            'has_post_data': op['has_post_data'],
            'attached_count': len(op.get('attached', [])),
            'note': '',
        }

        # 低置信度标注
        if op['confidence'] < 0.7:
            item['note'] = '自动识别置信度较低，建议确认或修改函数名'
            suggestions.append(
                '操作 #{} ({}) 识别置信度较低，建议确认是否为 "{}"'.format(
                    i + 1, op['path'], op['description']
                )
            )

        review_items.append(item)

    # 生成摘要
    total = len(operations)
    merged_count = len(merged_items)
    final_count = len(review_items)

    summary = '从 HAR 中识别到 {} 个操作'.format(total)
    if merged_count > 0:
        summary += '，合并后为 {} 个'.format(final_count)

    if diff:
        summary += '（新增 {} 个，已存在 {} 个）'.format(
            diff['new_count'], diff['existing_count']
        )

    return {
        'summary': summary,
        'operations': review_items,
        'merged': merged_items,
        'diff': diff,
        'suggestions': suggestions,
    }


def _confidence_level(confidence):
    """置信度等级"""
    if confidence >= 0.8:
        return 'high'
    elif confidence >= 0.6:
        return 'medium'
    else:
        return 'low'


def _detect_mergeable_operations(operations):
    """
    检测可合并的操作。

    规则：
    - 同类型的相邻操作合并（如 GET /login.html + POST /api/auth/login）
    - paginate 操作合并到相邻的 list 操作
    - 相同路径的 GET 请求合并（不同参数）
    """
    merge_map = {}  # { 被合并的index: 目标index }

    for i, op in enumerate(operations):
        # 同类型合并：GET 页面 + POST API（如 login.html + /api/auth/login）
        if op['op_type'] in ('login', 'logout') and op['method'] == 'GET':
            for j in range(i + 1, len(operations)):
                if j in merge_map:
                    continue
                if operations[j]['op_type'] == op['op_type'] and operations[j]['method'] == 'POST':
                    merge_map[i] = j
                    break

        if op['op_type'] == 'paginate':
            # 向前找最近的 list 操作
            for j in range(i - 1, -1, -1):
                if j in merge_map:
                    continue
                if operations[j]['op_type'] == 'list' and operations[j]['resource_name'] == op['resource_name']:
                    merge_map[i] = j
                    break

        # 相同路径的 GET 请求，且都是 list/detail 类型
        if op['op_type'] in ('list', 'detail') and op['method'] == 'GET':
            for j in range(i - 1, -1, -1):
                if j in merge_map:
                    continue
                prev = operations[j]
                if (prev['op_type'] == op['op_type']
                        and prev['resource_name'] == op['resource_name']
                        and prev['method'] == 'GET'):
                    merge_map[i] = j
                    break

    return merge_map


def _compute_diff(new_operations, existing_operations):
    """
    计算新操作与已有操作的差异。

    Args:
        new_operations: 本次分析出的操作列表
        existing_operations: 已有的操作列表（来自 site_config.json）

    Returns:
        dict: {
            'new_count': int,
            'existing_count': int,
            'new_ops': [ 新增操作 ],
            'existing_ops': [ 已存在操作 ],
            'changed_ops': [ 变更操作 ]
        }
    """
    existing_map = {op['name']: op for op in existing_operations}

    new_ops = []
    existing_ops = []
    changed_ops = []

    for op in new_operations:
        func_name = op['func_name']
        if func_name in existing_map:
            existing_op = existing_map[func_name]
            # 检查参数是否变化
            new_params = _extract_param_names(op)
            old_params = existing_op.get('params', [])
            if sorted(new_params) != sorted(old_params):
                changed_ops.append({
                    'name': func_name,
                    'old_params': old_params,
                    'new_params': new_params,
                })
            existing_ops.append(func_name)
        else:
            new_ops.append({
                'name': func_name,
                'op_type': op['op_type'],
                'description': op['description'],
                'path': op['path'],
                'method': op['method'],
            })

    return {
        'new_count': len(new_ops),
        'existing_count': len(existing_ops),
        'new_ops': new_ops,
        'existing_ops': existing_ops,
        'changed_ops': changed_ops,
    }


def _extract_param_names(op):
    """从操作中提取参数名列表"""
    params = []

    # Query 参数
    query_params = op.get('primary', {}).get('query_params', {})
    params.extend(query_params.keys())

    # POST Body 参数
    post_data = op.get('primary', {}).get('post_data')
    if post_data:
        if 'json' in post_data and isinstance(post_data['json'], dict):
            params.extend(post_data['json'].keys())
        if 'form' in post_data:
            params.extend(post_data['form'].keys())
        if 'params' in post_data:
            params.extend(post_data['params'].keys())

    return sorted(set(params))


def format_review_text(review_data):
    """
    将确认清单格式化为用户可读的文本。

    Args:
        review_data: present_for_review() 的输出

    Returns:
        str: 格式化的确认文本
    """
    lines = []
    lines.append(review_data['summary'])
    lines.append('')

    # 增量对比信息
    diff = review_data.get('diff')
    if diff and diff.get('new_count', 0) > 0:
        lines.append('【增量对比】')
        lines.append('  已存在: {} 个操作'.format(diff['existing_count']))
        lines.append('  新增: {} 个操作'.format(diff['new_count']))
        if diff.get('changed_ops'):
            lines.append('  参数变更: {} 个操作'.format(len(diff['changed_ops'])))
        lines.append('')

    # 操作列表
    lines.append('【识别到的操作】')
    for item in review_data['operations']:
        conf_mark = {'high': '✓', 'medium': '~', 'low': '?'}[item['confidence_level']]
        write_mark = '★' if item['is_write'] else ' '
        attached = ' (+{} 附属请求)'.format(item['attached_count']) if item['attached_count'] else ''

        line = '  {} {}. {}({}) — {} {}{}'.format(
            write_mark,
            item['index'],
            item['func_name'],
            ', '.join(_get_param_preview(item)),
            item['method'],
            item['path'],
            attached,
        )
        lines.append(line)

        if item['note']:
            lines.append('     ⚠ {}'.format(item['note']))

    # 合并说明
    if review_data['merged']:
        lines.append('')
        lines.append('【已合并的操作】')
        for m in review_data['merged']:
            lines.append('  · {} → 合并到操作 #{}'.format(
                m['func_name'], m['merged_into_index'] + 1
            ))

    # 建议
    if review_data['suggestions']:
        lines.append('')
        lines.append('【建议】')
        for s in review_data['suggestions']:
            lines.append('  · {}'.format(s))

    return '\n'.join(lines)


def _get_param_preview(item):
    """获取参数预览（从 path 和 method 推断）"""
    # 简化：从 path 中提取参数名
    # 实际参数提取由 param_extractor 完成
    params = []
    if item['op_type'] == 'login':
        params = ['username', 'password']
    elif item['op_type'] == 'list':
        params = ['page']
    elif item['op_type'] == 'detail':
        params = ['id']
    return params


def main():
    parser = argparse.ArgumentParser(description='生成操作确认清单')
    parser.add_argument('input', help='semantic_labeler 输出的 JSON 文件')
    parser.add_argument('-o', '--output', help='输出 JSON 文件路径')
    parser.add_argument('--existing', help='已有 site_config.json 路径（增量学习）')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        labeled_data = json.load(f)

    existing_operations = None
    if args.existing:
        with open(args.existing, 'r', encoding='utf-8') as f:
            site_config = json.load(f)
            existing_operations = site_config.get('operations', [])

    result = present_for_review(labeled_data, existing_operations)

    # 输出格式化文本
    text = format_review_text(result)
    print(text)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print('\n[OK] 已保存至: {}'.format(args.output))


if __name__ == '__main__':
    main()
