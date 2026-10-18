"""CI syntax and annotation guard for the Mini's Python 3.9 runtime."""
import ast
from pathlib import Path


def test_cluster_python39_compatible():
    for path in Path(__file__).resolve().parents[1].rglob('*.py'):
        tree = ast.parse(path.read_text(), filename=str(path), feature_version=(3, 9))
        postponed = any(isinstance(n, ast.ImportFrom) and n.module == '__future__'
                        and any(a.name == 'annotations' for a in n.names) for n in tree.body)
        if postponed:
            continue
        for node in ast.walk(tree):
            annotations = []
            if isinstance(node, ast.AnnAssign):
                annotations.append(node.annotation)
            if isinstance(node, ast.arg) and node.annotation:
                annotations.append(node.annotation)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
                annotations.append(node.returns)
            for annotation in annotations:
                assert not any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)
                               for n in ast.walk(annotation)), str(path)


def test_cluster_usage_dependency_imports():
    # Runtime type aliases are evaluated even with postponed annotations.
    from cluster import live_metrics
    assert callable(live_metrics.aggregate)
