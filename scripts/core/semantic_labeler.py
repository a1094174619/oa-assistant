# -*- coding: utf-8 -*-
"""
语义标注器

为操作组添加语义标签（login / send / approve / ...），
推断函数名和操作描述。

兼容: Python 3.8+, Windows 7
"""
import json
import argparse
import re
from typing import Dict, List, Optional, Any
from urllib.parse import urlparse

# 内建语义映射表：URL 路径关键词 → 操作类型
PATH_SEMANTIC_MAP = [
    # (关键词列表, 操作类型, 函数名前缀, 中文描述)
    (['login', 'signin', 'sign-in', 'auth'], 'login', 'login', '登录'),
    (['logout', 'signout', 'sign-out'], 'logout', 'logout', '登出'),
    (['send', 'compose', 'write', 'create', 'new', 'add', 'post'], 'create', 'create', '创建/发送'),
    (['forward', 'fw', 'resend'], 'forward', 'forward', '转发'),
    (['reply', 'respond', 'response'], 'reply', 'reply', '回复'),
    (['list', 'inbox', 'search', 'query', 'find', 'filter'], 'list', 'get_list', '获取列表'),
    (['detail', 'view', 'read', 'get', 'info', 'show'], 'detail', 'get_detail', '查看详情'),
    (['delete', 'remove', 'destroy', 'cancel'], 'delete', 'delete', '删除'),
    (['update', 'edit', 'modify', 'change', 'put', 'patch'], 'update', 'update', '更新/编辑'),
    (['approve', 'pass', 'accept', 'confirm', 'agree'], 'approve', 'approve', '审批/通过'),
    (['reject', 'deny', 'refuse', 'decline', 'return'], 'reject', 'reject', '驳回/退回'),
    (['submit', 'apply', 'request', 'commit'], 'submit', 'submit', '提交/申请'),
    (['archive', 'file', 'store', 'save'], 'archive', 'archive', '归档/保存'),
    (['upload', 'import', 'attach'], 'upload', 'upload', '上传/导入'),
    (['download', 'export', 'print'], 'download', 'download', '下载/导出'),
    (['page', 'paginate', 'next', 'prev', 'scroll'], 'paginate', 'go_page', '翻页'),
    (['sign', 'check', 'verify', 'validate'], 'sign', 'sign', '签收/验证'),
    (['register', 'signup', 'enroll'], 'register', 'register', '注册'),
    (['reset', 'refresh', 'reload'], 'reset', 'reset', '重置/刷新'),
    (['copy', 'clone', 'duplicate'], 'copy', 'copy', '复制'),
    (['move', 'transfer', 'assign'], 'transfer', 'transfer', '转移/指派'),
    (['lock', 'unlock', 'freeze'], 'lock', 'lock', '锁定/解锁'),
    (['share', 'publish', 'deploy'], 'publish', 'publish', '发布/共享'),
]

# POST Body 字段名 → 操作类型推断
BODY_SEMANTIC_MAP = [
    (['username', 'password', 'captcha'], 'login', 'login', '登录'),
    (['to', 'subject', 'body', 'content'], 'send', 'send', '发送'),
    (['recipient', 'cc', 'bcc'], 'send', 'send', '发送'),
    (['opinion', 'comment', 'reason'], 'approve', 'approve', '审批'),
]

# 中文关键词 → 操作类型
CHINESE_SEMANTIC_MAP = [
    (['登录', '签到'], 'login', 'login', '登录'),
    (['发送', '发邮件', '写信', '新建', '创建', '新增'], 'create', 'create', '创建/发送'),
    (['转发', '转交'], 'forward', 'forward', '转发'),
    (['回复', '答复'], 'reply', 'reply', '回复'),
    (['删除', '移除', '取消'], 'delete', 'delete', '删除'),
    (['审批', '通过', '同意', '批准'], 'approve', 'approve', '审批/通过'),
    (['驳回', '退回', '拒绝', '不同意'], 'reject', 'reject', '驳回/退回'),
    (['提交', '申请', '上报'], 'submit', 'submit', '提交/申请'),
    (['查询', '搜索', '列表', '收件箱'], 'list', 'get_list', '获取列表'),
    (['查看', '详情', '阅读'], 'detail', 'get_detail', '查看详情'),
    (['编辑', '修改', '更新'], 'update', 'update', '更新/编辑'),
    (['归档', '存档', '保存'], 'archive', 'archive', '归档/保存'),
    (['签收', '签报'], 'sign', 'sign', '签收'),
    (['发文', '发公文'], 'create', 'create', '创建/发送'),
    (['收文', '收件'], 'list', 'get_list', '获取列表'),
    (['翻页', '下一页', '上一页'], 'paginate', 'go_page', '翻页'),
]


def _match_path_keywords(path):
    """根据 URL 路径匹配语义"""
    path_lower = path.lower()
    matches = []

    for keywords, op_type, func_prefix, desc in PATH_SEMANTIC_MAP:
        for kw in keywords:
            if kw in path_lower:
                matches.append({
                    'type': op_type,
                    'func_prefix': func_prefix,
                    'description': desc,
                    'matched_keyword': kw,
                    'confidence': 0.7 if len(kw) <= 3 else 0.9,
                })
                break  # 每组只匹配一次

    return matches


def _match_body_keywords(post_data):
    """根据 POST Body 字段名匹配语义"""
    if not post_data:
        return []

    # 收集所有字段名
    field_names = set()
    if 'json' in post_data:
        if isinstance(post_data['json'], dict):
            field_names.update(post_data['json'].keys())
    if 'form' in post_data:
        field_names.update(post_data['form'].keys())
    if 'params' in post_data:
        field_names.update(post_data['params'].keys())

    field_names_lower = {f.lower() for f in field_names}

    matches = []
    for keywords, op_type, func_prefix, desc in BODY_SEMANTIC_MAP:
        overlap = set(kw.lower() for kw in keywords) & field_names_lower
        if overlap:
            matches.append({
                'type': op_type,
                'func_prefix': func_prefix,
                'description': desc,
                'matched_fields': sorted(overlap),
                'confidence': 0.8,
            })

    return matches


def _match_chinese_keywords(text):
    """根据中文关键词匹配语义"""
    if not text:
        return []

    matches = []
    for keywords, op_type, func_prefix, desc in CHINESE_SEMANTIC_MAP:
        for kw in keywords:
            if kw in text:
                matches.append({
                    'type': op_type,
                    'func_prefix': func_prefix,
                    'description': desc,
                    'matched_keyword': kw,
                    'confidence': 0.85,
                })
                break

    return matches


def _infer_resource_name(path):
    """
    从 URL 路径推断资源名称。

    /api/mail/send → mail
    /api/user/login → user
    /api/budget/submit → budget
    /auth/token → auth
    """
    parts = [p for p in path.strip('/').split('/') if p and not _is_common_prefix(p)]

    if not parts:
        return 'resource'

    # 取第一个非通用前缀的段
    for part in parts:
        if not _is_common_prefix(part) and not _looks_like_id(part):
            return part.lower()

    return 'resource'


def _is_common_prefix(s):
    """判断是否为通用前缀（api, v1, v2, rest, etc.）"""
    common = {'api', 'rest', 'v1', 'v2', 'v3', 'v4', 'web', 'app',
              'service', 'svc', 'gateway', 'gw', 'proxy', 'backend'}
    return s.lower() in common


def _looks_like_id(s):
    """判断是否像 ID"""
    if s.isdigit():
        return True
    if len(s) >= 8 and '-' in s:
        return True
    if len(s) >= 16 and s.isalnum():
        return True
    return False


def _build_agent_context(op):
    """
    为低置信度操作构建 AGENT 分析上下文。

    收集足够的信息让 AGENT 能够推断操作的语义：
    - 完整的 URL、路径、方法
    - 请求头中的关键信息
    - POST body 结构
    - 响应结构
    - 前后操作的关联

    Returns:
        dict: AGENT 分析所需的上下文
    """
    primary = op.get('primary', {})
    post_data = primary.get('post_data')
    response = primary.get('response', {})

    context = {
        'method': op.get('method', ''),
        'url': op.get('url', ''),
        'path': op.get('path', ''),
        'host': op.get('host', ''),
        'is_write': op.get('is_write', False),
        'is_api': primary.get('is_api', False),
        'is_document': primary.get('is_document', False),
        'query_params': primary.get('query_params', {}),
        'header_keys': sorted(primary.get('headers', {}).keys()),
        'response_status': response.get('status', 0),
        'response_mime': response.get('mime_type', ''),
        'header_sources': primary.get('header_sources', {}),
    }

    # POST body 结构（脱敏）
    if post_data:
        body_info = {'mime_type': post_data.get('mime_type', '')}
        if 'json' in post_data and isinstance(post_data['json'], dict):
            body_info['json_keys'] = sorted(post_data['json'].keys())
        if 'form' in post_data:
            body_info['form_keys'] = sorted(post_data['form'].keys())
        context['post_body'] = body_info

    # 响应结构（脱敏）
    body_json = response.get('body_json')
    if isinstance(body_json, dict):
        context['response_body_keys'] = sorted(body_json.keys())

    return context


def _generate_function_name(op_type, resource_name, func_prefix, method, path):
    """
    生成函数名。

    优先级：
    1. 基于语义的 func_prefix + resource_name
    2. 基于 method + resource_name
    3. 基于 path 的简化
    """
    # 特殊处理：login/logout 不需要资源名
    if op_type in ('login', 'logout', 'register'):
        return op_type

    # 组合: func_prefix + resource_name
    if func_prefix and resource_name:
        name = '{}_{}'.format(func_prefix, resource_name)
        # 去重：send_send → send
        parts = name.split('_')
        deduped = [parts[0]]
        for p in parts[1:]:
            if p != deduped[-1]:
                deduped.append(p)
        return '_'.join(deduped)

    # 基于 method
    method_map = {
        'GET': 'get',
        'POST': 'create',
        'PUT': 'update',
        'PATCH': 'update',
        'DELETE': 'delete',
    }
    action = method_map.get(method, 'do')
    if resource_name:
        return '{}_{}'.format(action, resource_name)

    # 基于 path
    path_parts = [p for p in path.strip('/').split('/') if p and not _is_common_prefix(p)]
    if path_parts:
        return '_'.join(path_parts[-2:]) if len(path_parts) >= 2 else path_parts[0]

    return 'unknown_operation'


def label_operations(boundary_data):
    """
    为操作组添加语义标签。

    Args:
        boundary_data: boundary_detector.detect_boundaries() 的输出

    Returns:
        dict: {
            'meta': { 操作数量 },
            'operations': [ 带标签的操作列表 ]
        }
    """
    groups = boundary_data.get('groups', [])
    operations = []

    for group in groups:
        primary = group.get('primary', {})
        method = primary.get('method', 'GET')
        path = primary.get('path', '/')
        post_data = primary.get('post_data')
        url = primary.get('url', '')

        # 收集所有匹配结果
        all_matches = []

        # 路径关键词匹配
        path_matches = _match_path_keywords(path)
        all_matches.extend(path_matches)

        # Body 字段匹配
        body_matches = _match_body_keywords(post_data)
        all_matches.extend(body_matches)

        # 中文关键词匹配（从 URL、路径、Header 等中搜索）
        text_to_search = ' '.join([
            path,
            primary.get('headers', {}).get('Referer', ''),
            url,
        ])
        chinese_matches = _match_chinese_keywords(text_to_search)
        all_matches.extend(chinese_matches)

        # 选择最佳匹配（confidence 最高）
        best_match = None
        if all_matches:
            all_matches.sort(key=lambda m: m.get('confidence', 0), reverse=True)
            best_match = all_matches[0]

        # 推断资源名
        resource_name = _infer_resource_name(path)

        # 确定操作类型
        if best_match:
            op_type = best_match['type']
            func_prefix = best_match['func_prefix']
            description = best_match['description']
            confidence = best_match['confidence']
            matched_keyword = best_match.get('matched_keyword', best_match.get('matched_fields', []))
        else:
            # 无法匹配，根据 method 推断
            if method == 'POST':
                op_type = 'create'
                func_prefix = 'create'
                description = '提交/创建'
            elif method == 'PUT' or method == 'PATCH':
                op_type = 'update'
                func_prefix = 'update'
                description = '更新/编辑'
            elif method == 'DELETE':
                op_type = 'delete'
                func_prefix = 'delete'
                description = '删除'
            else:
                op_type = 'read'
                func_prefix = 'get'
                description = '查询/获取'
            confidence = 0.4
            matched_keyword = None

        # 生成函数名
        func_name = _generate_function_name(op_type, resource_name, func_prefix, method, path)

        # 构建操作对象
        operation = {
            'group_index': group.get('group_index', 0),
            'op_type': op_type,
            'func_name': func_name,
            'description': description,
            'confidence': round(confidence, 2),
            'matched_keyword': matched_keyword,
            'resource_name': resource_name,
            'primary': primary,
            'attached': group.get('attached', []),
            'method': method,
            'path': path,
            'url': url,
            'host': primary.get('host', ''),
            'is_write': primary.get('is_write', False),
            'has_post_data': post_data is not None,
        }

        operations.append(operation)

    # 函数名去重：如果出现同名，追加序号
    name_count = {}
    for op in operations:
        name = op['func_name']
        if name in name_count:
            name_count[name] += 1
            op['func_name'] = '{}_{}'.format(name, name_count[name])
        else:
            name_count[name] = 0

    # 为低置信度操作收集上下文（供 AGENT 智能分析使用）
    for op in operations:
        if op['confidence'] < 0.7:
            op['agent_context'] = _build_agent_context(op)

    return {
        'meta': {
            'operation_count': len(operations),
            'high_confidence': sum(1 for op in operations if op['confidence'] >= 0.7),
            'low_confidence': sum(1 for op in operations if op['confidence'] < 0.7),
        },
        'operations': operations,
    }


def main():
    parser = argparse.ArgumentParser(description='为操作组添加语义标签')
    parser.add_argument('input', help='boundary_detector 输出的 JSON 文件')
    parser.add_argument('-o', '--output', help='输出 JSON 文件路径')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        boundary_data = json.load(f)

    result = label_operations(boundary_data)

    print("[INFO] 操作数量: {}".format(result['meta']['operation_count']))
    print("[INFO] 高置信度: {}, 低置信度: {}".format(
        result['meta']['high_confidence'], result['meta']['low_confidence']))

    for op in result['operations']:
        conf = '✓' if op['confidence'] >= 0.7 else '?'
        print("  {} {} → {}() [{}] {}".format(
            conf, op['method'].ljust(6), op['func_name'].ljust(25),
            op['op_type'], op['description']
        ))

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print("[OK] 已保存至: {}".format(args.output))


if __name__ == '__main__':
    main()
