# -*- coding: utf-8 -*-
"""
增量对比器

对比本次分析结果与已有接口，识别新增、变更、未变的操作。

兼容: Python 3.8+, Windows 7
"""
import json
import argparse
from typing import Dict, List, Optional, Any


def check_diff(labeled_data, site_config_path):
    """
    对比分析结果与已有站点配置。

    Args:
        labeled_data: semantic_labeler 的输出
        site_config_path: 已有 site_config.json 的路径

    Returns:
        dict: {
            'site_id': str,
            'new_operations': [ 新增操作 ],
            'changed_operations': [ 变更操作 ],
            'unchanged_operations': [ 未变操作 ],
            'removed_operations': [ 已有但本次未出现的操作 ],
            'summary': str
        }
    """
    with open(site_config_path, 'r', encoding='utf-8') as f:
        site_config = json.load(f)

    existing_ops = {op['name']: op for op in site_config.get('operations', [])}
    new_ops = {op['func_name']: op for op in labeled_data.get('operations', [])}

    new_operations = []
    changed_operations = []
    unchanged_operations = []
    removed_operations = []

    # 检查新操作和变更
    for name, op in new_ops.items():
        if name not in existing_ops:
            new_operations.append({
                'name': name,
                'op_type': op['op_type'],
                'description': op['description'],
                'path': op['path'],
                'method': op['method'],
            })
        else:
            existing = existing_ops[name]
            # 对比路径和方法
            if (existing.get('path') != op['path']
                    or existing.get('method', '').upper() != op['method'].upper()):
                changed_operations.append({
                    'name': name,
                    'old_path': existing.get('path', ''),
                    'new_path': op['path'],
                    'old_method': existing.get('method', ''),
                    'new_method': op['method'],
                })
            else:
                unchanged_operations.append(name)

    # 检查被删除的操作（已有但本次未出现）
    for name in existing_ops:
        if name not in new_ops:
            removed_operations.append({
                'name': name,
                'description': existing_ops[name].get('description', ''),
            })

    summary = '新增 {} 个, 变更 {} 个, 未变 {} 个, 缺失 {} 个'.format(
        len(new_operations), len(changed_operations),
        len(unchanged_operations), len(removed_operations)
    )

    return {
        'site_id': site_config.get('id', 'unknown'),
        'new_operations': new_operations,
        'changed_operations': changed_operations,
        'unchanged_operations': unchanged_operations,
        'removed_operations': removed_operations,
        'summary': summary,
    }


def merge_operations(existing_config, labeled_data, confirmed_new_ops):
    """
    将确认后的新操作合并到已有配置中。

    Args:
        existing_config: 已有 site_config.json 内容
        labeled_data: 本次分析结果
        confirmed_new_ops: 用户确认的新操作函数名列表

    Returns:
        dict: 更新后的 site_config
    """
    existing_ops = list(existing_config.get('operations', []))
    existing_names = {op['name'] for op in existing_ops}

    new_ops = labeled_data.get('operations', [])
    for op in new_ops:
        if op['func_name'] in confirmed_new_ops and op['func_name'] not in existing_names:
            existing_ops.append({
                'name': op['func_name'],
                'op_type': op['op_type'],
                'description': op['description'],
                'path': op['path'],
                'method': op['method'],
                'params': [],  # 将由 param_extractor 填充
            })

    existing_config['operations'] = existing_ops
    return existing_config


def main():
    parser = argparse.ArgumentParser(description='增量对比')
    parser.add_argument('input', help='semantic_labeler 输出的 JSON 文件')
    parser.add_argument('--config', required=True, help='已有 site_config.json 路径')
    parser.add_argument('-o', '--output', help='输出 JSON 文件路径')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        labeled_data = json.load(f)

    result = check_diff(labeled_data, args.config)

    print("[INFO] 站点: {}".format(result['site_id']))
    print("[INFO] {}".format(result['summary']))

    if result['new_operations']:
        print("\n[新增操作]")
        for op in result['new_operations']:
            print("  + {} ({}) — {} {}".format(
                op['name'], op['description'], op['method'], op['path']
            ))

    if result['changed_operations']:
        print("\n[变更操作]")
        for op in result['changed_operations']:
            print("  ~ {} : {} {} → {} {}".format(
                op['name'],
                op['old_method'], op['old_path'],
                op['new_method'], op['new_path']
            ))

    if result['removed_operations']:
        print("\n[缺失操作（本次HAR中未出现）]")
        for op in result['removed_operations']:
            print("  - {} ({})".format(op['name'], op['description']))

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print('\n[OK] 已保存至: {}'.format(args.output))


if __name__ == '__main__':
    main()
