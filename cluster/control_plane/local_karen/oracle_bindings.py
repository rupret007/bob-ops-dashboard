"""Recognize read-only static contract modules without executing their source.

This proves an intramodule binding premise, assuming the qualified runtime's
standard library. It does not prove external import provenance or grant review
authority. Unknown imports, calls and mutations are refused in complete source.
"""
import ast


def readonly_oracle_bindings(tree):
    """Refuse module code that can replace or introspect its oracle bindings."""
    if not isinstance(tree, ast.Module):
        return False
    methods = {'read_text', 'resolve', 'is_file', 'count', 'index', 'find',
               'startswith', 'partition', 'casefold', 'split', 'splitlines',
               'items', 'group', 'join', 'lower'}
    regex = {'search', 'findall', 'escape', 'fullmatch', 'M', 'MULTILINE'}
    attributes = methods | regex | {'parent', 'parents', 'access', 'X_OK'}
    builtins = {'set', 'len', 'dict', 'str'}
    bindings, imports, imported, stores, functions = set(), set(), set(), set(), set()
    for statement in tree.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and type(statement.value.value) is str:
            continue
        if isinstance(statement, ast.Import):
            if len(statement.names) != 1 or statement.names[0].asname or statement.names[0].name not in {'os', 're'}:
                return False
            name = statement.names[0].name
            imports.add(id(statement))
            imported.add(name)
        elif isinstance(statement, ast.ImportFrom):
            if statement.level or len(statement.names) != 1 or statement.names[0].asname:
                return False
            if (statement.module, statement.names[0].name) not in {('pathlib', 'Path'), ('__future__', 'annotations')}:
                return False
            imports.add(id(statement))
            if statement.module == '__future__':
                continue
            name = 'Path'
            imported.add(name)
        elif isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                return False
            name = statement.targets[0].id
            stores.add(id(statement.targets[0]))
            if name in {'os', 're', 'Path'}:
                return False
        elif isinstance(statement, ast.FunctionDef):
            name = statement.name
            if name in {'os', 're', 'Path'}:
                return False
            functions.add(name)
            args = statement.args
            if (statement.decorator_list or args.defaults or args.kw_defaults
                    or args.posonlyargs or args.kwonlyargs or args.vararg or args.kwarg
                    or name.startswith('test_') and args.args):
                return False
        else:
            return False
        if name in bindings or name in builtins or name.startswith('__'):
            return False
        bindings.add(name)
    protected = bindings | builtins | {'Path', 'os', 're', '__file__'}
    known = protected | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    known |= {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
    parents = {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Lambda, ast.Global,
                             ast.Nonlocal, ast.With, ast.AsyncWith, ast.Delete,
                             ast.Try, ast.Raise, ast.Yield, ast.YieldFrom, ast.Await)):
            return False
        if isinstance(node, ast.FunctionDef) and node not in tree.body:
            return False
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in imports:
            return False
        if isinstance(node, ast.arg) and node.arg in protected:
            return False
        if isinstance(node, ast.Name):
            if node.id not in known or node.id.startswith('__') and node.id != '__file__':
                return False
            if not isinstance(node.ctx, ast.Load) and node.id in protected and id(node) not in stores:
                return False
            parent = parents.get(id(node))
            if isinstance(node.ctx, ast.Load) and node.id in {'re', 'os', 'Path'} and node.id not in imported:
                return False
            if isinstance(node.ctx, ast.Load) and node.id in {'re', 'os'} and not (
                    isinstance(parent, ast.Attribute) and parent.value is node):
                return False
            if isinstance(node.ctx, ast.Load) and node.id == 'Path' and not (
                    isinstance(parent, ast.Call) and parent.func is node):
                return False
        if isinstance(node, ast.Attribute):
            if not isinstance(node.ctx, ast.Load) or node.attr not in attributes:
                return False
            if isinstance(node.value, ast.Name):
                if node.value.id == 're' and node.attr not in regex:
                    return False
                if node.value.id == 'os' and node.attr not in {'access', 'X_OK'}:
                    return False
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in functions | {'Path', 'set', 'len', 'dict'}:
                    return False
            elif not isinstance(node.func, ast.Attribute):
                return False
    return True
