# -*- coding: utf-8 -*-
"""
站点匹配器

根据用户意图文本匹配知识库中的站点和操作。

兼容: Python 3.8+, Windows 7
"""
import json
import os
import argparse
from typing import Dict, List, Optional, Any


def _normalize_text(text):
    """文本归一化：小写、去空格"""
    return text.lower().strip()


def _keyword_match_score(text, keywords):
    """
    计算文本与关键词列表的匹配得分。

    完全匹配 → 1.0
    包含匹配 → 0.8
    部分匹配 → 0.5
    无匹配   → 0.0
    """
    text_lower = _normalize_text(text)
    max_score = 0.0

    for kw in keywords:
        kw_lower = _normalize_text(kw)
        if not kw_lower:
            continue

        if text_lower == kw_lower:
            return 1.0
        elif kw_lower in text_lower or text_lower in kw_lower:
            score = 0.8
        else:
            # 字符级匹配
            common = sum(1 for c in kw_lower if c in text_lower)
            score = common / max(len(kw_lower), 1) * 0.5

        if score > max_score:
            max_score = score

    return max_score


def _operation_match_score(text, operation):
    """
    计算文本与操作的匹配得分。
    """
    scores = []

    # 函数名匹配
    func_name = operation.get('name', '')
    scores.append(_keyword_match_score(text, [func_name]) * 0.6)

    # 操作类型匹配
    op_type = operation.get('op_type', '')
    type_keywords = {
        'login': ['登录', 'login', '签到'],
        'logout': ['登出', 'logout'],
        'create': ['创建', '新建', '发送', '提交', 'send', 'create', 'add'],
        'list': ['列表', '查询', '搜索', 'list', 'search', 'query', '收件箱'],
        'detail': ['详情', '查看', 'detail', 'view', 'read'],
        'update': ['修改', '编辑', 'update', 'edit'],
        'delete': ['删除', 'delete', 'remove'],
        'approve': ['审批', '通过', 'approve', 'accept'],
        'reject': ['驳回', '退回', 'reject', 'deny'],
        'forward': ['转发', 'forward'],
        'reply': ['回复', 'reply'],
        'submit': ['提交', '申请', 'submit', 'apply'],
    }
    if op_type in type_keywords:
        scores.append(_keyword_match_score(text, type_keywords[op_type]) * 0.8)

    # 描述匹配
    description = operation.get('description', '')
    if description:
        desc_keywords = [w for w in description if len(w) > 1]
        if desc_keywords:
            scores.append(_keyword_match_score(text, desc_keywords) * 0.5)

    return max(scores) if scores else 0.0


def match_site(text, index_path):
    """
    根据用户意图文本匹配站点。

    Args:
        text: 用户意图文本
        index_path: _index.json 文件路径

    Returns:
        dict or None: {
            'site_id': str,
            'site_name': str,
            'site_path': str,
            'match_score': float,
            'matched_operation': str or None,
            'operation_score': float
        }
    """
    if not os.path.exists(index_path):
        return None

    with open(index_path, 'r', encoding='utf-8') as f:
        index = json.load(f)

    sites = index.get('sites', [])
    if not sites:
        return None

    results = []

    for site in sites:
        # 站点名称匹配
        name_score = _keyword_match_score(text, [site.get('name', '')])

        # 别名匹配
        aliases = site.get('aliases', [])
        alias_score = _keyword_match_score(text, aliases) if aliases else 0.0

        # URL 匹配
        base_urls = site.get('base_urls', [])
        url_score = _keyword_match_score(text, base_urls) if base_urls else 0.0

        # 描述匹配
        desc = site.get('description', '')
        desc_score = _keyword_match_score(text, [desc]) if desc else 0.0

        site_score = max(name_score, alias_score, url_score, desc_score)

        # 操作匹配
        operations = site.get('operations', [])
        best_op = None
        best_op_score = 0.0

        for op in operations:
            op_score = _operation_match_score(text, op)
            if op_score > best_op_score:
                best_op_score = op_score
                best_op = op.get('name')

        results.append({
            'site_id': site.get('id', ''),
            'site_name': site.get('name', ''),
            'site_path': site.get('path', ''),
            'match_score': site_score,
            'matched_operation': best_op,
            'operation_score': best_op_score,
        })

    # 排序：先按站点匹配分，再按操作匹配分
    results.sort(key=lambda r: (r['match_score'], r['operation_score']), reverse=True)

    # 阈值过滤
    best = results[0] if results else None
    if best and best['match_score'] < 0.3:
        return None

    return best


def list_sites(index_path):
    """列出所有已学习的站点"""
    if not os.path.exists(index_path):
        return []

    with open(index_path, 'r', encoding='utf-8') as f:
        index = json.load(f)

    return index.get('sites', [])


def get_site_operations(index_path, site_id):
    """获取指定站点的操作列表"""
    sites = list_sites(index_path)
    for site in sites:
        if site.get('id') == site_id:
            return site.get('operations', [])
    return []


def delete_site(site_id, oa_sites_dir):
    """
    删除指定站点，包括目录和索引条目。

    Args:
        site_id: 站点标识
        oa_sites_dir: oa_sites 目录路径

    Returns:
        dict: { success, deleted_files, message }
    """
    import shutil
    from datetime import datetime

    index_path = os.path.join(oa_sites_dir, '_index.json')
    site_dir = os.path.join(oa_sites_dir, site_id)

    deleted_files = []

    # 删除站点目录
    if os.path.exists(site_dir):
        shutil.rmtree(site_dir)
        deleted_files.append(site_dir)

    # 从索引中移除
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)

        original_count = len(index.get('sites', []))
        index['sites'] = [s for s in index.get('sites', []) if s.get('id') != site_id]
        removed = original_count - len(index['sites'])

        if removed > 0:
            index['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(index_path, 'w', encoding='utf-8') as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            deleted_files.append(index_path + ' (entry)')

    if deleted_files:
        return {
            'success': True,
            'deleted_files': deleted_files,
            'message': '已删除站点: {}'.format(site_id),
        }
    else:
        return {
            'success': False,
            'deleted_files': [],
            'message': '站点不存在: {}'.format(site_id),
        }


def main():
    parser = argparse.ArgumentParser(description='站点管理')
    parser.add_argument('text', nargs='?', default='', help='用户意图文本')
    parser.add_argument('--index', help='_index.json 路径')
    parser.add_argument('--list', action='store_true', help='列出所有站点')
    parser.add_argument('--delete', metavar='SITE_ID', help='删除指定站点')
    args = parser.parse_args()

    # 默认路径: scripts/core/ → scripts/ → oa-assistant/ → oa_sites/
    oa_sites_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'oa_sites')
    if not args.index:
        args.index = os.path.join(oa_sites_dir, '_index.json')

    if args.list:
        sites = list_sites(args.index)
        if not sites:
            print('[INFO] 站点库为空，尚未学习任何系统')
        else:
            print('[INFO] 已学习 {} 个系统:'.format(len(sites)))
            for site in sites:
                ops = site.get('operations', [])
                op_names = [op['name'] for op in ops]
                print('  · {} ({}) — {}'.format(
                    site.get('name', ''),
                    site.get('id', ''),
                    ', '.join(op_names) if op_names else '无操作'
                ))
        return

    if args.delete:
        result = delete_site(args.delete, oa_sites_dir)
        if result['success']:
            print('[OK] {}'.format(result['message']))
            for f in result['deleted_files']:
                print('  已删除: {}'.format(f))
        else:
            print('[FAIL] {}'.format(result['message']))
        return

    if not args.text:
        parser.print_help()
        return

    result = match_site(args.text, args.index)

    if result:
        print('[匹配] 站点: {} ({})'.format(result['site_name'], result['site_id']))
        print('       匹配度: {:.0%}'.format(result['match_score']))
        if result['matched_operation']:
            print('       操作: {} ({:.0%})'.format(
                result['matched_operation'], result['operation_score']))
    else:
        print('[未匹配] 站点库中没有匹配的系统，需要学习')


if __name__ == '__main__':
    main()
