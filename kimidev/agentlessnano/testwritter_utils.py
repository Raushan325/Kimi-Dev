import ast
import re
import json
from collections import defaultdict
from kimidev.agentlessnano.utils import get_full_file_paths_and_classes_and_functions
from kimidev.agentlessnano.post_process import remove_comments_and_docstrings


def normalize_test(test: str):
    def normalize_code(code):
        try:
            node = ast.parse(code)
            return ast.unparse(node)
        except:
            return code

    test = normalize_code(test)

    try:
        remove_docstring_test = remove_comments_and_docstrings(test)
        ast.parse(remove_docstring_test)  # check
    except:
        remove_docstring_test = test

    try:
        test_name = remove_docstring_test.splitlines()[-1].split('(')[0]
        remove_docstring_test = remove_docstring_test.replace(
            test_name,
            'test_func',
        )  # use generic name
    except:
        pass

    return remove_docstring_test


def create_patch_from_code(python_code: str) -> str:
    patch_header = """diff --git a/reproduce_bug.py b/reproduce_bug.py
new file mode 100644
index 0000000..e69de29
"""
    patch_body = []
    patch_body.append('--- /dev/null')
    patch_body.append('+++ b/reproduce_bug.py')

    code_lines = python_code.split('\n')
    patch_body.append(f'@@ -0,0 +1,{len(code_lines)} @@')

    for line in code_lines:
        patch_body.append(f'+{line}')

    return patch_header + '\n'.join(patch_body) + '\n'


def extract_first_code_block(text):
    pattern = re.compile(r'```python(.*?)```', re.DOTALL)

    match = pattern.search(text)

    if match:
        return match.group(1).strip()

    return None


def extract_test_code(text):
    return text.split('```python')[1].split('```')[0].strip()


def replace_test_functions(original_code, test_cases):
    # 构建类路径到方法的映射
    test_case_map = defaultdict(list)
    for case in test_cases:
        parts = case.split('::')
        # 分割类路径和方法名
        if len(parts) == 1:
            # 顶级函数的情况
            class_path = ()
            method_name = parts[0]
        else:
            # 处理类嵌套的情况（如 ClassA::ClassB::method）
            *class_parts, method_name = parts
            class_path = tuple(class_parts)
        test_case_map[class_path].append(method_name)

    # 解析原始代码为AST
    tree = ast.parse(original_code)

    class TestReplacer(ast.NodeTransformer):
        def __init__(self, case_map):
            self.case_map = case_map
            self.class_stack = []  # 记录当前类嵌套路径

        def visit_ClassDef(self, node):
            # 进入类时记录类名
            self.class_stack.append(node.name)
            node = self.generic_visit(node)  # 递归处理子节点
            self.class_stack.pop()
            return node

        def visit_FunctionDef(self, node):
            # 获取当前类路径（元组形式）
            current_class = tuple(self.class_stack)
            
            # 检查是否匹配需要替换的测试用例
            if current_class in self.case_map:
                if node.name in self.case_map[current_class]:
                    if (
                        len(node.body) >= 1 and 
                        isinstance(node.body[0], ast.Expr) and 
                        isinstance(node.body[0].value, ast.Str)
                    ):
                        # remain doc string
                        node.body = [node.body[0], ast.Pass()]
                    else:
                        node.body = [ast.Pass()]
            
            # 继续处理嵌套函数
            self.generic_visit(node)
            return node

    # 应用AST转换
    replacer = TestReplacer(test_case_map)
    modified_tree = replacer.visit(tree)
    ast.fix_missing_locations(modified_tree)

    # 将AST转换回代码
    return ast.unparse(modified_tree)

def remove_test_cases(structure, f2p_list):
    files, classes, functions = get_full_file_paths_and_classes_and_functions(structure)
    file_contentes_dict = dict()
    for filepath, test_cases in f2p_list.items():
        content = None

        for file_content in files:
            if file_content[0] == filepath:
                if(len(file_content[1]) > 0 and file_content[1][0].endswith('\n')):
                    content = "".join(file_content[1])
                else:
                    content = "\n".join(file_content[1])
                file_contentes_dict[filepath] = {
                    'old_content': content,
                    'new_content': replace_test_functions(content, test_cases)
                }
                break

        # assert content is not None, "file not found"
    return file_contentes_dict