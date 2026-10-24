"""Complete-source proof for inert scanner fixtures; unknown syntax fails closed."""
import re


def _python_test_fixture_source(diff):
    """Mask proven inert fixture expressions in complete Python test source.

    Calls, computed values, incomplete context and neighboring statements stay
    visible. Secret scanners always receive the original source.
    """
    import ast

    files, path, number = {}, '', 1
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            path = line.split(' b/', 1)[-1]
        elif line.startswith('@@'):
            match = re.search(r'\+(\d+)(?:,\d+)? @@', line)
            number = int(match.group(1)) if match else 1
        elif line.startswith((' ', '+')) and not line.startswith('+++'):
            files.setdefault(path, {})[number] = line[1:]
            number += 1
    masked = {}
    for path, lines in files.items():
        approved_modules = {
            'cluster/control_plane/local_karen/tests/test_shell_display.py',
            'cluster/control_plane/local_karen/tests/test_python_fixture_source.py',
            'cluster/control_plane/tests/test_fix_difficulty.py',
        }
        if (path not in approved_modules
                or set(lines) != set(range(1, max(lines) + 1))):
            continue
        source = '\n'.join(lines[n] for n in range(1, max(lines) + 1))
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        # Captures and exception aliases need not contain Name(Store). Unknown
        # syntax cannot establish that a fixture consumer is inert.
        grammar = {'Module', 'Expr', 'Constant', 'Import', 'ImportFrom', 'alias',
                   'Assign', 'AugAssign', 'FunctionDef', 'arguments', 'arg',
                   'Assert', 'Return', 'For', 'Name', 'Attribute', 'Call',
                   'BinOp', 'BoolOp', 'Compare', 'List', 'Tuple', 'Dict', 'Set',
                   'GeneratorExp', 'comprehension', 'keyword', 'Load', 'Store',
                   'Add', 'Mult', 'And', 'Or', 'Eq', 'NotEq', 'In', 'NotIn',
                   'UnaryOp', 'Not'}
        grammar = {getattr(ast, name) for name in grammar}
        if any(type(node) not in grammar for node in ast.walk(tree)):
            continue
        # These regression harnesses only build PRInfo and invoke pure checks.
        # Any unknown import, alias or callable invalidates that proof.
        imports = {
            ('', 'pytest'),
            ('cluster.control_plane.local_karen', 'checks'),
            ('cluster.control_plane.local_karen.config', 'KarenConfig'),
            ('cluster.control_plane.fix_dispatcher', 'classify_fix_difficulty'),
        }
        safe_calls = {'candidate', 'scanner', 'len', 'KarenConfig', 'classify_fix_difficulty',
                      'pytest.mark.parametrize', 'checks.PRInfo',
                      'checks.check_no_dangerous_commands', 'checks.check_no_secrets_in_diff',
                      'checks.check_security_patterns', 'checks.run_static_security_scan',
                      'source.splitlines', 'pr.diff.replace'}
        protected = {'candidate', 'scanner', 'len', 'KarenConfig',
                     'classify_fix_difficulty', 'pytest', 'checks'}

        def inline_data(node):
            return (isinstance(node, ast.Constant)
                    or isinstance(node, (ast.List, ast.Tuple, ast.Set))
                    and all(inline_data(item) for item in node.elts)
                    or isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                    and inline_data(node.left) and inline_data(node.right))

        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        invalid = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                parameters = set()
                for decorator in node.decorator_list:
                    pure = (isinstance(decorator, ast.Call)
                            and ast.unparse(decorator.func) == 'pytest.mark.parametrize'
                            and len(decorator.args) == 2 and not decorator.keywords
                            and isinstance(decorator.args[0], ast.Constant)
                            and isinstance(decorator.args[0].value, str)
                            and inline_data(decorator.args[1]))
                    if not pure:
                        invalid = True
                    else:
                        parameters.update(name.strip() for name in decorator.args[0].value.split(','))
                args = node.args
                if args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg:
                    invalid = True
                if node.name != 'candidate' and (not node.name.startswith('test_')
                        or args.defaults or {arg.arg for arg in args.args} != parameters):
                    invalid = True
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, 'module', '') or ''
                if (getattr(node, 'level', 0)
                        or any(alias.asname or (module, alias.name) not in imports for alias in node.names)):
                    invalid = True
            elif isinstance(node, ast.arg) and node.arg in protected:
                invalid = True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in protected:
                canonical = (isinstance(node, ast.FunctionDef) and node.name == 'candidate'
                             and not node.decorator_list and all(inline_data(item) for item in node.args.defaults)
                             and len(node.args.args) == 2
                             and node.args.args[0].arg in ('line', 'source')
                             and node.args.args[1].arg == 'path'
                             and all(isinstance(item, (ast.Assign, ast.AugAssign, ast.Return))
                                     for item in node.body)
                             and isinstance(node.body[-1], ast.Return)
                             and isinstance(node.body[-1].value, ast.Call)
                             and ast.unparse(node.body[-1].value.func) == 'checks.PRInfo')
                if not canonical:
                    invalid = True
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.id in protected:
                parent = parents.get(node)
                scanner_loop = (node.id == 'scanner' and isinstance(parent, ast.For)
                                and parent.target is node and isinstance(parent.iter, (ast.Tuple, ast.List))
                                and bool(parent.iter.elts)
                                and all(isinstance(item, ast.Attribute)
                                        and ast.unparse(item) in safe_calls
                                        and ast.unparse(item).startswith('checks.check_')
                                        or isinstance(item, ast.Attribute)
                                        and ast.unparse(item) == 'checks.run_static_security_scan'
                                        for item in parent.iter.elts))
                if not scanner_loop:
                    invalid = True
            elif (isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
                  and node.id.startswith(('__', 'test_', 'pytest_'))):
                invalid = True
            elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
                parent = parents.get(node)
                fixture_diff = (ast.unparse(node) == 'pr.diff' and isinstance(parent, ast.Assign)
                                and isinstance(parent.value, ast.Call)
                                and ast.unparse(parent.value.func) == 'pr.diff.replace')
                if not fixture_diff:
                    invalid = True
            elif isinstance(node, ast.Call):
                literal_format = (isinstance(node.func, ast.Attribute)
                                  and node.func.attr in ('format', 'join')
                                  and isinstance(node.func.value, ast.Constant)
                                  and isinstance(node.func.value.value, str))
                if not literal_format and ast.unparse(node.func) not in safe_calls:
                    invalid = True
        if invalid:
            continue
        names, spans = set(), []

        def literal(node):
            return (isinstance(node, ast.Constant)
                    or isinstance(node, ast.Name) and node.id in names
                    or isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                    and literal(node.left) and literal(node.right)
                    or isinstance(node, (ast.List, ast.Tuple, ast.Set))
                    and all(literal(item) for item in node.elts))

        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id.isupper() and literal(node.value)):
                names.add(node.targets[0].id)
                spans.append(node)
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and literal(node.iter):
                spans.append(node.iter)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and ast.unparse(node.func) == 'pytest.mark.parametrize'):
                spans.extend(arg for arg in node.args if literal(arg))
        for node in spans:
            for n in range(node.lineno, node.end_lineno + 1):
                value = masked.get((path, n), lines[n])
                # AST columns are UTF-8 byte offsets; Python slices use characters.
                start = (len(lines[n].encode()[:node.col_offset].decode())
                         if n == node.lineno else 0)
                end = (len(lines[n].encode()[:node.end_col_offset].decode())
                       if n == node.end_lineno else len(value))
                masked[path, n] = value[:start] + ' ' * (end - start) + value[end:]
    return masked
