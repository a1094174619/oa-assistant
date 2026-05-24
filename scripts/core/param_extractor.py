# -*- coding: utf-8 -*-
"""
参数提取器

从操作组中提取函数参数，标记参数类型（variable/fixed/derived），
推断 Python 类型注解。

兼容: Python 3.8+, Windows 7
"""
import json
import argparse
import re
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import urlparse, unquote

# 已知的固定值参数（不应暴露为函数参数）
FIXED_PARAM_NAMES = frozenset([
    'pagesize', 'page_size', 'limit', 'per_page',
    'sort', 'orderby', 'order_by',
    'format', 'type', 'action',
    'csrfmiddlewaretoken', '_token', '_csrf',
    'utf8', 'authenticity_token',
])

# 已知的敏感参数（应从配置读取，不硬编码）
SENSITIVE_PARAM_NAMES = frozenset([
    'password', 'passwd', 'pwd', 'secret',
    'token', 'accesstoken', 'access_token',
    'apikey', 'api_key', 'key',
    'captcha', 'vericode', 'verify_code',
])

# 已知的分页参数（应暴露为函数参数）
PAGINATION_PARAMS = frozenset([
    'page', 'pageno', 'page_no', 'pagenum', 'page_num',
    'offset', 'skip', 'start',
    'cursor', 'nexttoken', 'next_token',
])

# Python 类型推断映射
TYPE_INFERENCE_RULES = [
    # (参数名模式, Python类型, 默认值)
    (re.compile(r'^(page|pageno|page_num|pagenum|offset|limit|pagesize|page_size|per_page|size|count|num|total)$', re.I), 'int', None),
    (re.compile(r'^(id|_id|.*_id)$', re.I), 'str', None),
    (re.compile(r'^(is_|has_|can_|should_|enable|disable|flag)', re.I), 'bool', None),
    (re.compile(r'^(date|time|datetime|start_date|end_date|created_at|updated_at)', re.I), 'str', None),
    (re.compile(r'^(email|mail|username|user|account|login)', re.I), 'str', None),
    (re.compile(r'^(url|link|href|src|redirect|callback)', re.I), 'str', None),
    (re.compile(r'^(phone|mobile|tel)', re.I), 'str', None),
    (re.compile(r'^(amount|money|price|cost|fee|salary|budget)', re.I), 'float', None),
]


def _infer_python_type(param_name, param_value=None):
    """
    推断参数的 Python 类型。

    Args:
        param_name: 参数名
        param_value: 参数值（可选，用于辅助判断）

    Returns:
        str: Python 类型字符串
    """
    name_lower = param_name.lower()

    # 基于参数名模式
    for pattern, py_type, default in TYPE_INFERENCE_RULES:
        if pattern.match(name_lower):
            return py_type

    # 基于参数值
    if param_value is not None:
        if isinstance(param_value, bool):
            return 'bool'
        if isinstance(param_value, int):
            return 'int'
        if isinstance(param_value, float):
            return 'float'
        if isinstance(param_value, (list, tuple)):
            return 'list'
        if isinstance(param_value, dict):
            return 'dict'

    return 'str'


def _is_fixed_param(name, value):
    """判断参数是否为固定值（不应暴露为函数参数）"""
    name_lower = name.lower()

    # 已知的固定参数名
    if name_lower in FIXED_PARAM_NAMES:
        return True

    # 空值或布尔值
    if value in ('', 'true', 'false', '1', '0', 'null', 'none'):
        return True

    return False


def _is_sensitive_param(name):
    """判断参数是否为敏感参数"""
    return name.lower() in SENSITIVE_PARAM_NAMES


def _is_pagination_param(name):
    """判断参数是否为分页参数"""
    return name.lower() in PAGINATION_PARAMS


def _classify_param(name, value, is_from_header=False):
    """
    对参数进行分类。

    Returns:
        str: 'variable' | 'fixed' | 'derived' | 'sensitive'
    """
    if _is_sensitive_param(name):
        return 'sensitive'

    # 分页参数始终为 variable
    if _is_pagination_param(name):
        return 'variable'

    if is_from_header:
        # Header 中的参数多数是 derived（如 Authorization）
        auth_headers = {'authorization', 'cookie', 'x-csrf-token', 'x-xsrf-token',
                        'referer', 'origin'}
        if name.lower() in auth_headers:
            return 'derived'
        return 'fixed'

    if _is_fixed_param(name, value):
        return 'fixed'

    return 'variable'


def _extract_path_params(path):
    """
    从 URL 路径中提取路径参数。

    /api/mail/detail/123 → {'id': '123'}
    /api/user/abc/profile → {'user_id': 'abc'}
    """
    params = {}
    parts = [p for p in path.strip('/').split('/') if p]

    for i, part in enumerate(parts):
        # 数字段
        if part.isdigit():
            # 用前一段作为参数名
            if i > 0 and not parts[i - 1].isdigit():
                param_name = '{}_id'.format(parts[i - 1].lower())
            else:
                param_name = 'id'
            params[param_name] = part

        # UUID 段
        elif len(part) == 36 and part.count('-') == 4:
            if i > 0:
                param_name = '{}_id'.format(parts[i - 1].lower())
            else:
                param_name = 'id'
            params[param_name] = part

    return params


def extract_params(labeled_data):
    """
    从语义标注后的操作中提取参数。

    Args:
        labeled_data: semantic_labeler.label_operations() 的输出

    Returns:
        dict: {
            'operations': [ 带参数的操作列表 ]
        }
    """
    operations = labeled_data.get('operations', [])
    result_ops = []

    for op in operations:
        primary = op.get('primary', {})
        method = op.get('method', 'GET')
        path = op.get('path', '/')
        post_data = primary.get('post_data')
        query_params = primary.get('query_params', {})
        headers = primary.get('headers', {})

        all_params = []

        # 1. 路径参数
        path_params = _extract_path_params(path)
        for name, value in path_params.items():
            all_params.append({
                'name': name,
                'source': 'path',
                'value': value,
                'classification': 'variable',
                'python_type': _infer_python_type(name, value),
                'required': True,
            })

        # 2. Query 参数
        for name, value in query_params.items():
            classification = _classify_param(name, value)
            py_type = _infer_python_type(name, value)

            # 分页参数特殊处理
            default_value = None
            if _is_pagination_param(name):
                default_value = 1 if py_type == 'int' else None

            all_params.append({
                'name': name,
                'source': 'query',
                'value': value,
                'classification': classification,
                'python_type': py_type,
                'required': classification == 'variable',
                'default': default_value,
            })

        # 3. POST Body 参数
        if post_data:
            # 文件上传参数
            file_parts = post_data.get('files')
            if file_parts:
                for fp in file_parts:
                    all_params.append({
                        'name': fp.get('name', 'file'),
                        'source': 'file',
                        'value': fp.get('filename', ''),
                        'classification': 'variable',
                        'python_type': 'str',
                        'required': True,
                        'is_file': True,
                        'file_content_type': fp.get('content_type', ''),
                    })

            # JSON body
            json_body = post_data.get('json')
            if isinstance(json_body, dict):
                for name, value in json_body.items():
                    classification = _classify_param(name, value)
                    all_params.append({
                        'name': name,
                        'source': 'body_json',
                        'value': value,
                        'classification': classification,
                        'python_type': _infer_python_type(name, value),
                        'required': classification == 'variable',
                    })

            # Form data
            form_data = post_data.get('form')
            if isinstance(form_data, dict):
                for name, value in form_data.items():
                    classification = _classify_param(name, value)
                    all_params.append({
                        'name': name,
                        'source': 'body_form',
                        'value': value,
                        'classification': classification,
                        'python_type': _infer_python_type(name, value),
                        'required': classification == 'variable',
                    })

            # HAR params
            har_params = post_data.get('params')
            if isinstance(har_params, dict):
                for name, value in har_params.items():
                    classification = _classify_param(name, value)
                    all_params.append({
                        'name': name,
                        'source': 'body_params',
                        'value': value,
                        'classification': classification,
                        'python_type': _infer_python_type(name, value),
                        'required': classification == 'variable',
                    })

        # 4. 关键 Header 参数（只提取必要的）
        for name, value in headers.items():
            name_lower = name.lower()
            if name_lower in ('authorization', 'x-csrf-token', 'x-xsrf-token'):
                all_params.append({
                    'name': name_lower.replace('-', '_'),
                    'source': 'header',
                    'value': value,
                    'classification': 'derived',
                    'python_type': 'str',
                    'required': False,
                })

        # 构建函数签名
        variable_params = [p for p in all_params if p['classification'] == 'variable']
        fixed_params = [p for p in all_params if p['classification'] == 'fixed']
        derived_params = [p for p in all_params if p['classification'] == 'derived']
        sensitive_params = [p for p in all_params if p['classification'] == 'sensitive']

        # 生成函数签名字符串
        sig_parts = []
        for p in variable_params:
            type_str = p['python_type']
            default = p.get('default')
            if default is not None:
                sig_parts.append('{}={}'.format(p['name'], default))
            else:
                sig_parts.append(p['name'])

        # 敏感参数放最后，带默认值
        for p in sensitive_params:
            sig_parts.append('{}=None'.format(p['name']))

        func_signature = ', '.join(sig_parts)

        result_op = dict(op)  # 复制原有字段
        result_op.update({
            'params': all_params,
            'variable_params': variable_params,
            'fixed_params': fixed_params,
            'derived_params': derived_params,
            'sensitive_params': sensitive_params,
            'func_signature': func_signature,
            'is_file_upload': post_data.get('is_file_upload', False) if post_data else False,
            'is_file_download': primary.get('response', {}).get('is_file_download', False),
        })

        result_ops.append(result_op)

    return {
        'operations': result_ops,
    }


def main():
    parser = argparse.ArgumentParser(description='提取操作参数')
    parser.add_argument('input', help='semantic_labeler 输出的 JSON 文件')
    parser.add_argument('-o', '--output', help='输出 JSON 文件路径')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        labeled_data = json.load(f)

    result = extract_params(labeled_data)

    for op in result['operations']:
        var_params = [p['name'] for p in op['variable_params']]
        fixed_params = [p['name'] for p in op['fixed_params']]
        sensitive = [p['name'] for p in op['sensitive_params']]

        print("  {}({})".format(op['func_name'], ', '.join(var_params) if var_params else ''))
        if fixed_params:
            print("    固定参数: {}".format(', '.join(fixed_params)))
        if sensitive:
            print("    敏感参数: {}".format(', '.join(sensitive)))

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print('\n[OK] 已保存至: {}'.format(args.output))


if __name__ == '__main__':
    main()
