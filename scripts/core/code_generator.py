# -*- coding: utf-8 -*-
"""
代码生成器

从参数提取后的操作列表生成 Python 接口代码。
使用内置模板引擎（不依赖 Jinja2，兼容 Python 3.8）。

兼容: Python 3.8+, Windows 7
"""
import json
import os
import argparse
import re
from typing import Dict, List, Optional, Any
from datetime import datetime
from urllib.parse import urlparse


def _to_class_name(site_id):
    """将 site_id 转换为类名: mail_oa → MailOA"""
    parts = site_id.replace('-', '_').split('_')
    return ''.join(p.capitalize() for p in parts if p)


def _to_snake_case(name):
    """转换为 snake_case"""
    s1 = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', name)
    return re.sub(r'([a-z\d])([A-Z])', r'\1_\2', s1).lower()


def _build_path_template(path, path_params):
    """
    将路径中的参数段替换为 Python 格式化占位符。

    /api/mail/detail/123 → /api/mail/detail/{id}
    """
    parts = path.strip('/').split('/')
    param_names = {p['value']: p['name'] for p in path_params}

    result_parts = []
    for part in parts:
        if part in param_names:
            result_parts.append('{{{}}}'.format(param_names[part]))
        elif part.isdigit() and len(part) <= 20:
            # 数字段但未在 path_params 中，可能是遗漏
            result_parts.append(part)
        else:
            result_parts.append(part)

    return '/' + '/'.join(result_parts)


def _build_request_args(op):
    """
    构建请求参数代码。

    根据参数来源生成对应的 requests 调用参数。
    """
    method = op.get('method', 'GET')
    variable_params = op.get('variable_params', [])
    fixed_params = op.get('fixed_params', [])
    derived_params = op.get('derived_params', [])
    sensitive_params = op.get('sensitive_params', [])
    post_data = op.get('primary', {}).get('post_data', {})
    query_params = op.get('primary', {}).get('query_params', {})

    args_lines = []

    # Query 参数
    query_var_params = [p for p in variable_params if p['source'] == 'query']
    query_fixed_params = [p for p in fixed_params if p['source'] == 'query']

    if query_var_params or query_fixed_params:
        params_dict_parts = []
        for p in query_var_params:
            params_dict_parts.append("'{}': {}".format(p['name'], p['name']))
        for p in query_fixed_params:
            params_dict_parts.append("'{}': {}".format(p['name'], repr(p['value'])))
        args_lines.append('params={{{}}}'.format(', '.join(params_dict_parts)))

    # Body 参数
    body_var_params = [p for p in variable_params if p['source'] in ('body_json', 'body_form', 'body_params')]
    body_fixed_params = [p for p in fixed_params if p['source'] in ('body_json', 'body_form', 'body_params')]
    sensitive_body_params = [p for p in sensitive_params if p['source'] in ('body_json', 'body_form', 'body_params')]

    if body_var_params or body_fixed_params or sensitive_body_params:
        body_parts = []
        for p in body_var_params:
            body_parts.append("'{}': {}".format(p['name'], p['name']))
        for p in body_fixed_params:
            body_parts.append("'{}': {}".format(p['name'], repr(p['value'])))
        for p in sensitive_body_params:
            body_parts.append("'{}': {}".format(p['name'], p['name']))

        # 判断用 json 还是 data
        mime = post_data.get('mime_type', '') if post_data else ''
        if 'json' in mime:
            args_lines.append('json={{{}}}'.format(', '.join(body_parts)))
        else:
            args_lines.append('data={{{}}}'.format(', '.join(body_parts)))

    # Header 参数 - derived 参数不生成（由 session 自动管理）
    # Authorization 等 header 在 login 方法中设置到 session
    # 此处不输出 header 参数

    return ', '.join(args_lines)


def _build_func_params(op):
    """构建函数参数列表（带类型注解）"""
    parts = ['self']

    variable_params = op.get('variable_params', [])
    sensitive_params = op.get('sensitive_params', [])
    is_file_upload = op.get('is_file_upload', False)
    is_file_download = op.get('is_file_download', False)

    for p in variable_params:
        # 文件上传参数：参数名为文件路径
        if p.get('is_file') or p.get('source') == 'file':
            parts.append('{}: str'.format(p['name']))
            continue
        type_str = p['python_type']
        default = p.get('default')
        if default is not None:
            parts.append('{}: {} = {}'.format(p['name'], type_str, repr(default)))
        else:
            parts.append('{}: {}'.format(p['name'], type_str))

    for p in sensitive_params:
        type_str = p['python_type']
        parts.append('{}: {} = None'.format(p['name'], type_str))

    # 文件下载方法：添加 save_path 参数
    if is_file_download:
        parts.append('save_path: str = None')

    return ', '.join(parts)


def _build_login_method(base_url, login_op):
    """生成 login 方法"""
    if not login_op:
        return _generate_default_login(base_url)

    path = _build_path_template(login_op['path'], [p for p in login_op.get('params', []) if p['source'] == 'path'])
    method = login_op.get('method', 'POST')

    # 收集登录操作的敏感参数和变量参数（作为登录字段）
    sensitive_params = login_op.get('sensitive_params', [])
    variable_params = [p for p in login_op.get('variable_params', []) if p['source'] in ('body_json', 'body_form', 'body_params', 'query')]
    # 合并：敏感参数优先，变量参数补充
    login_fields = {}
    for p in sensitive_params:
        login_fields[p['name']] = p
    for p in variable_params:
        if p['name'] not in login_fields:
            login_fields[p['name']] = p

    # 如果没有从 HAR 中提取到字段，回退到 username/password
    if not login_fields:
        login_fields = {
            'username': {'name': 'username', 'python_type': 'str'},
            'password': {'name': 'password', 'python_type': 'str'},
        }

    lines = []
    # 函数签名：所有字段均为可选参数
    func_params = ['self']
    for p in login_fields.values():
        func_params.append('{}: {} = None'.format(p['name'], p.get('python_type', 'str')))
    lines.append('    def login({}) -> dict:'.format(', '.join(func_params)))
    lines.append('        """登录（支持 Cookie 兜底）"""')

    # Cookie 兜底：优先检查凭证中是否有 cookies
    lines.append('        creds = self._load_credentials()')
    lines.append('        if self._load_cookies_from_credentials(creds):')
    lines.append('            self._logged_in = True')
    lines.append("            return {'status': 'cookie_loaded', 'message': '已从凭证文件加载Cookie，跳过登录'}")

    # 从凭证文件加载未传入的字段
    for p in login_fields.values():
        name = p['name']
        lines.append('        {0} = {0} if {0} is not None else creds.get("{0}", "")'.format(name))

    # 密码类字段通过 _encrypt_password() 加密
    password_field_names = ('password', 'passwd', 'pwd', 'pass', 'userpwd',
                            'login_password', 'user_password')
    for p in login_fields.values():
        name = p['name']
        if any(n in name.lower() for n in password_field_names):
            lines.append('        {} = self._encrypt_password({})'.format(name, name))

    # 构建请求体
    body_parts = []
    for p in login_fields.values():
        body_parts.append("'{}': {}".format(p['name'], p['name']))
    body_str = '{{{}}}'.format(', '.join(body_parts))

    if method == 'POST':
        post_data = login_op.get('primary', {}).get('post_data', {})
        mime = post_data.get('mime_type', '') if post_data else ''

        if 'json' in mime:
            lines.append("        resp = self._request('POST', '{}',".format(path))
            lines.append('            json={})'.format(body_str))
        elif 'form' in mime or 'urlencoded' in mime:
            lines.append("        resp = self._request('POST', '{}',".format(path))
            lines.append('            data={})'.format(body_str))
        else:
            lines.append("        resp = self._request('POST', '{}',".format(path))
            lines.append('            json={})'.format(body_str))
    else:
        lines.append("        resp = self._request('{}', '{}',".format(method, path))
        lines.append('            params={})'.format(body_str))

    # 登录成功后自动设置 token 到 session
    lines.append("        if 'token' in resp:")
    lines.append("            self.session.headers['Authorization'] = 'Bearer ' + resp['token']")
    lines.append("        self._logged_in = True")
    lines.append('        return resp')
    return '\n'.join(lines)


def _generate_default_login(base_url):
    """生成默认 login 方法（当 HAR 中没有登录操作时）"""
    lines = []
    lines.append('    def login(self, username: str = None, password: str = None, **kwargs) -> dict:')
    lines.append('        """登录（支持 Cookie 兜底）"""')
    lines.append('        creds = self._load_credentials()')
    lines.append('        if self._load_cookies_from_credentials(creds):')
    lines.append('            self._logged_in = True')
    lines.append("            return {'status': 'cookie_loaded', 'message': '已从凭证文件加载Cookie，跳过登录'}")
    lines.append('        username = username if username is not None else creds.get("username", "")')
    lines.append('        password = password if password is not None else creds.get("password", "")')
    lines.append('        password = self._encrypt_password(password)')
    lines.append('        extra = {k: v for k, v in creds.items() if k not in ("username", "password")}')
    lines.append('        extra.update(kwargs)')
    lines.append("        resp = self._request('POST', '/api/auth/login',")
    lines.append("            json={'username': username, 'password': password, **extra})")
    lines.append('        return resp')
    return '\n'.join(lines)


def _build_method(op, base_url):
    """为单个操作生成方法代码"""
    func_name = op['func_name']
    func_params = _build_func_params(op)
    description = op.get('description', '')
    method = op.get('method', 'GET')
    path_template = _build_path_template(op['path'], [p for p in op.get('params', []) if p['source'] == 'path'])
    request_args = _build_request_args(op)

    is_file_upload = op.get('is_file_upload', False)
    is_file_download = op.get('is_file_download', False)

    # 检查路径中是否有参数占位符 {xxx}
    path_params_in_template = re.findall(r'\{(\w+)\}', path_template)

    lines = []
    lines.append('    def {}({}) -> dict:'.format(func_name, func_params))
    lines.append('        """{}"""'.format(description))

    # 构建路径表达式
    if path_params_in_template:
        # 用 format() 方法替换路径参数
        format_template = path_template.replace('{', '{{').replace('}', '}}')
        for param_name in path_params_in_template:
            format_template = format_template.replace('{{{{{}}}}}'.format(param_name), '{}')
        format_args = ', '.join(path_params_in_template)
        path_code = "'{}'.format({})".format(format_template, format_args)
    else:
        path_code = "'{}'".format(path_template)

    # 文件下载：使用 _download()
    if is_file_download:
        download_kwargs = []
        download_kwargs.append('save_path=save_path')
        # 添加 query 参数
        query_var_params = [p for p in op.get('variable_params', []) if p['source'] == 'query']
        query_fixed_params = [p for p in op.get('fixed_params', []) if p['source'] == 'query']
        if query_var_params or query_fixed_params:
            params_dict_parts = []
            for p in query_var_params:
                params_dict_parts.append("'{}': {}".format(p['name'], p['name']))
            for p in query_fixed_params:
                params_dict_parts.append("'{}': {}".format(p['name'], repr(p['value'])))
            download_kwargs.append('params={{{}}}'.format(', '.join(params_dict_parts)))

        lines.append("        resp = self._download({}, {})".format(path_code, ', '.join(download_kwargs)))
        lines.append('        return resp')
        return '\n'.join(lines)

    # 文件上传：使用 _upload()
    if is_file_upload:
        file_params = [p for p in op.get('variable_params', []) if p.get('is_file') or p.get('source') == 'file']
        non_file_var_params = [p for p in op.get('variable_params', []) if not (p.get('is_file') or p.get('source') == 'file')]
        non_file_fixed_params = [p for p in op.get('fixed_params', []) if p['source'] in ('body_json', 'body_form', 'body_params')]

        # 构建 file_paths 参数
        if len(file_params) == 1:
            lines.append("        resp = self._upload({}, ".format(path_code))
            lines.append("            file_paths={},".format(file_params[0]['name']))
        else:
            # 多文件字段：构建字典
            dict_parts = ["'{}': {}".format(p['name'], p['name']) for p in file_params]
            lines.append("        resp = self._upload({}, ".format(path_code))
            lines.append("            file_paths={{{}}},".format(', '.join(dict_parts)))

        # field_name（单文件时）
        if len(file_params) == 1:
            lines.append("            field_name='{}',".format(file_params[0]['name']))

        # 额外表单字段
        extra_fields_parts = []
        for p in non_file_var_params:
            extra_fields_parts.append("'{}': {}".format(p['name'], p['name']))
        for p in non_file_fixed_params:
            extra_fields_parts.append("'{}': {}".format(p['name'], repr(p['value'])))
        if extra_fields_parts:
            lines.append("            extra_fields={{{}}},".format(', '.join(extra_fields_parts)))

        # query 参数
        query_var_params = [p for p in non_file_var_params if p['source'] == 'query']
        query_fixed_params = [p for p in op.get('fixed_params', []) if p['source'] == 'query']
        if query_var_params or query_fixed_params:
            params_dict_parts = []
            for p in query_var_params:
                params_dict_parts.append("'{}': {}".format(p['name'], p['name']))
            for p in query_fixed_params:
                params_dict_parts.append("'{}': {}".format(p['name'], repr(p['value'])))
            lines.append("            params={{{}}}".format(', '.join(params_dict_parts)))

        lines.append("            )")
        lines.append('        return resp')
        return '\n'.join(lines)

    # 普通请求：使用 _request()
    if request_args:
        lines.append("        resp = self._request('{}', {},".format(method, path_code))
        lines.append('            {})'.format(request_args))
    else:
        lines.append("        resp = self._request('{}', {})".format(method, path_code))

    lines.append('        return resp')
    return '\n'.join(lines)


def generate_interface(params_data, site_id, site_name, base_url):
    """
    生成完整的接口 Python 代码。

    Args:
        params_data: param_extractor.extract_params() 的输出
        site_id: 站点标识 (如 mail_oa)
        site_name: 站点名称 (如 邮件办公系统)
        base_url: 基础 URL

    Returns:
        str: 生成的 Python 代码
    """
    operations = params_data.get('operations', [])
    class_name = _to_class_name(site_id)

    # 去重：相同功能只保留一个（POST 优先于 GET）
    # login/logout 只按 op_type 去重，其他按 (op_type, resource_name)
    seen_keys = {}
    for op in operations:
        op_type = op.get('op_type', '')
        resource_name = op.get('resource_name', '')
        # login/logout 只按 op_type 去重
        key = op_type if op_type in ('login', 'logout') else (op_type, resource_name)
        if key not in seen_keys:
            seen_keys[key] = op
        elif op.get('is_write') and not seen_keys[key].get('is_write'):
            seen_keys[key] = op
    operations = list(seen_keys.values())

    # 分离 login 操作和其他操作
    login_op = None
    other_ops = []
    for op in operations:
        if op.get('op_type') == 'login':
            if login_op is None:
                login_op = op
                # 确保 login 方法的函数名是 login
                if login_op['func_name'] != 'login':
                    login_op['func_name'] = 'login'
        else:
            other_ops.append(op)

    # 生成文件头
    code_lines = []
    code_lines.append('# -*- coding: utf-8 -*-')
    code_lines.append('"""')
    code_lines.append('{} - 自动生成接口'.format(site_name))
    code_lines.append('')
    code_lines.append('生成时间: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    code_lines.append('基础URL: {}'.format(base_url))
    code_lines.append('"""')
    code_lines.append('from typing import Optional, Dict, List, Any')
    code_lines.append('')
    code_lines.append('try:')
    code_lines.append('    from _base import BaseOAInterface')
    code_lines.append('except ImportError:')
    code_lines.append('    import os, sys')
    code_lines.append('    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))')
    code_lines.append('    from _base import BaseOAInterface')
    code_lines.append('')
    code_lines.append('')
    code_lines.append('class {}(BaseOAInterface):'.format(class_name))
    code_lines.append('    """{}"""'.format(site_name))
    code_lines.append('')

    # __init__
    init_default = '    def __init__(self, base_url: str = "{}", timeout: int = None, retries: int = None):'.format(base_url)
    code_lines.append(init_default)
    code_lines.append('        super().__init__(base_url, timeout=timeout, retries=retries, site_id="{}")'.format(site_id))
    code_lines.append('')

    # login 方法
    login_code = _build_login_method(base_url, login_op)
    code_lines.append(login_code)
    code_lines.append('')

    # 其他方法
    for op in other_ops:
        method_code = _build_method(op, base_url)
        code_lines.append(method_code)
        code_lines.append('')

    # 内置自测代码
    code_lines.append('')
    code_lines.extend(_build_self_test_block(class_name, site_id, other_ops))

    return '\n'.join(code_lines)


def _build_self_test_block(class_name, site_id, other_ops):
    """
    生成内置自测代码块（if __name__ == '__main__'）。

    支持：
    - dry-run: 构造请求但不发送，验证 URL 和参数组装
    - live: 真实发送请求（需要凭证）

    Returns:
        list[str]: 代码行列表
    """
    lines = []

    # 构建 dry-run 测试参数
    dry_run_calls = []
    for op in other_ops:
        func_name = op['func_name']
        var_params = op.get('variable_params', [])
        test_args = []
        for p in var_params:
            if p.get('python_type') == 'int':
                test_args.append('{}=1'.format(p['name']))
            else:
                test_args.append('{}="test"'.format(p['name']))
        dry_run_calls.append('            api.{}({})'.format(func_name, ', '.join(test_args)))

    lines.append('')
    lines.append('if __name__ == "__main__":')
    lines.append('    import sys')
    lines.append('    import json')
    lines.append('    import argparse')
    lines.append('')
    lines.append('    parser = argparse.ArgumentParser(description="{} 自测")'.format(class_name))
    lines.append('    parser.add_argument("--mode", choices=["dry-run", "live"], default="dry-run",')
    lines.append('                        help="测试模式: dry-run=构造请求不发送, live=真实发送")')
    lines.append('    parser.add_argument("--base-url", help="覆盖基础 URL")')
    lines.append('    parser.add_argument("--username", help="登录用户名 (live 模式)")')
    lines.append('    parser.add_argument("--password", help="登录密码 (live 模式)")')
    lines.append('    args = parser.parse_args()')
    lines.append('')
    lines.append('    base_url = args.base_url or "{}"'.format('https://example.com'))
    lines.append('    api = {}(base_url=base_url)'.format(class_name))
    lines.append('')
    lines.append('    if args.mode == "dry-run":')
    lines.append('        # Dry-Run: 拦截请求，验证构造正确性')
    lines.append('        captured = []')
    lines.append('        original_request = api.session.request')
    lines.append('')
    lines.append('        def mock_request(method, url, **kwargs):')
    lines.append('            captured.append({"method": method, "url": url, "kwargs": {')
    lines.append('                "params": kwargs.get("params"),')
    lines.append('                "json": kwargs.get("json"),')
    lines.append('                "data": kwargs.get("data"),')
    lines.append('            }})')
    lines.append('            class MockResp(object):')
    lines.append('                status_code = 200')
    lines.append('                text = \'{"code": 0, "msg": "ok", "data": {}}\'')
    lines.append('                def json(self):')
    lines.append('                    return json.loads(self.text)')
    lines.append('                def raise_for_status(self):')
    lines.append('                    pass')
    lines.append('            return MockResp()')
    lines.append('')
    lines.append('        api.session.request = mock_request')
    lines.append('        print("[Dry-Run] 测试请求构造...")')
    lines.append('        try:')
    lines.append('            api.login("test_user", "test_pass")')
    if dry_run_calls:
        for call in dry_run_calls:
            lines.append(call)
    lines.append('        except Exception as e:')
    lines.append('            print("[FAIL] 请求构造失败: {}".format(e))')
    lines.append('            sys.exit(1)')
    lines.append('')
    lines.append('        print("[OK] Dry-Run 通过，捕获 {} 个请求:".format(len(captured)))')
    lines.append('        for req in captured:')
    lines.append('            print("  {} {} params={} json={}".format(')
    lines.append('                req["method"], req["url"],')
    lines.append('                req["kwargs"].get("params"), req["kwargs"].get("json")))')
    lines.append('')
    lines.append('    elif args.mode == "live":')
    lines.append('        # Live: 真实发送请求')
    lines.append('        if not args.username or not args.password:')
    lines.append('            print("[错误] live 模式需要 --username 和 --password")')
    lines.append('            sys.exit(1)')
    lines.append('        print("[Live] 连接 {} ...".format(base_url))')
    lines.append('        try:')
    lines.append('            result = api.login(args.username, args.password)')
    lines.append('            print("[OK] 登录成功: {}".format(json.dumps(result, ensure_ascii=False)[:200]))')
    lines.append('        except Exception as e:')
    lines.append('            print("[FAIL] 登录失败: {}".format(e))')
    lines.append('            sys.exit(1)')

    return lines


def generate_site_config(params_data, site_id, site_name, base_url, aliases=None):
    """
    生成 site_config.json。

    Args:
        params_data: param_extractor 的输出
        site_id: 站点标识
        site_name: 站点名称
        base_url: 基础 URL
        aliases: 别名列表

    Returns:
        dict: site_config 内容
    """
    operations = []
    seen_keys = {}
    for op in params_data.get('operations', []):
        # 去重逻辑与 generate_interface 一致
        op_type = op.get('op_type', '')
        resource_name = op.get('resource_name', '')
        key = op_type if op_type in ('login', 'logout') else (op_type, resource_name)
        if key in seen_keys:
            # POST 优先
            if op.get('is_write') and not seen_keys[key].get('is_write'):
                # 替换
                operations = [o for o in operations if o['name'] != seen_keys[key]['func_name']]
            else:
                continue
        seen_keys[key] = op

        variable_params = [p['name'] for p in op.get('variable_params', [])]
        func_name = op['func_name']
        # login 操作统一命名为 login
        if op.get('op_type') == 'login' and func_name != 'login':
            func_name = 'login'
        operations.append({
            'name': func_name,
            'op_type': op['op_type'],
            'description': op['description'],
            'method': op['method'],
            'path': op['path'],
            'params': variable_params,
            'is_file_upload': op.get('is_file_upload', False),
            'is_file_download': op.get('is_file_download', False),
        })

    return {
        'id': site_id,
        'name': site_name,
        'aliases': aliases or [site_name],
        'description': '{}接口'.format(site_name),
        'base_urls': [base_url],
        'operations': operations,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'version': '1.0',
    }


def update_index(index_path, site_config):
    """
    更新 _index.json，添加或更新站点条目。

    Args:
        index_path: _index.json 文件路径
        site_config: site_config 内容
    """
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)
    else:
        index = {'version': '1.0', 'last_updated': '', 'sites': []}

    # 检查是否已存在
    site_id = site_config['id']
    existing_idx = None
    for i, site in enumerate(index['sites']):
        if site.get('id') == site_id:
            existing_idx = i
            break

    # 构建索引条目
    index_entry = {
        'id': site_id,
        'name': site_config['name'],
        'aliases': site_config.get('aliases', []),
        'description': site_config.get('description', ''),
        'base_urls': site_config.get('base_urls', []),
        'operations': [
            {'name': op['name'], 'params': op.get('params', [])}
            for op in site_config.get('operations', [])
        ],
        'path': 'oa_sites/{}/'.format(site_id),
    }

    if existing_idx is not None:
        index['sites'][existing_idx] = index_entry
    else:
        index['sites'].append(index_entry)

    index['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return index


def main():
    parser = argparse.ArgumentParser(description='生成 Python 接口代码')
    parser.add_argument('input', help='param_extractor 输出的 JSON 文件')
    parser.add_argument('--site-id', required=True, help='站点标识 (如 mail_oa)')
    parser.add_argument('--site-name', required=True, help='站点名称 (如 邮件办公系统)')
    parser.add_argument('--base-url', required=True, help='基础 URL')
    parser.add_argument('--aliases', nargs='*', help='站点别名')
    parser.add_argument('--oa-sites-dir', help='oa_sites 目录路径（自动写入）')
    args = parser.parse_args()

    with open(args.input, 'r', encoding='utf-8') as f:
        params_data = json.load(f)

    # 生成接口代码
    code = generate_interface(params_data, args.site_id, args.site_name, args.base_url)

    # 生成 site_config
    site_config = generate_site_config(
        params_data, args.site_id, args.site_name, args.base_url, args.aliases
    )

    # 输出
    if args.oa_sites_dir:
        site_dir = os.path.join(args.oa_sites_dir, args.site_id)
        os.makedirs(site_dir, exist_ok=True)

        # 写入 interface.py
        interface_path = os.path.join(site_dir, 'interface.py')
        with open(interface_path, 'w', encoding='utf-8') as f:
            f.write(code)
        print('[OK] 接口代码: {}'.format(interface_path))

        # 写入 site_config.json
        config_path = os.path.join(site_dir, 'site_config.json')
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(site_config, f, ensure_ascii=False, indent=2)
        print('[OK] 站点配置: {}'.format(config_path))

        # 更新 _index.json
        index_path = os.path.join(args.oa_sites_dir, '_index.json')
        update_index(index_path, site_config)
        print('[OK] 索引更新: {}'.format(index_path))
    else:
        print(code)

    print('\n[INFO] 生成 {} 个接口方法'.format(len(params_data.get('operations', []))))


if __name__ == '__main__':
    main()
