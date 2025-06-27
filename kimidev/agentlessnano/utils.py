import ast
import json
import logging
import os
import re
from collections import OrderedDict

import libcst as cst
import libcst.matchers as m


def load_json(filepath):
    return json.load(open(filepath, encoding='utf-8', errors='ignore'))


def merge_jsonl_files(jsonl_file_paths, output_file):
    # create output_file
    if not os.path.exists(output_file):
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
    with open(output_file, 'w', encoding='utf-8') as outfile:
        for file_path in jsonl_file_paths:
            with open(file_path, encoding='utf-8') as infile:
                for line in infile:
                    outfile.write(line)  # 直接写入，不修改格式


def remove_duplicates(input_dict):
    for file, lines in input_dict.items():
        # Use OrderedDict to remove duplicates while maintaining order
        input_dict[file] = list(OrderedDict.fromkeys(lines))
    return input_dict


def filter_out_test_files(structure):
    """filter out test files from the project structure"""
    for key, value in list(structure.items()):
        if key.startswith('test'):
            del structure[key]
        elif isinstance(value, dict):
            filter_out_test_files(value)


def filter_none_python(structure):
    for key, value in list(structure.items()):
        if isinstance(value, str):
            del structure[key]
            continue

        if (
            'functions' not in value.keys()
            and 'classes' not in value.keys()
            and 'text' not in value.keys()
        ) or (not len(value.keys()) == 3):
            filter_none_python(value)

            if structure[key] == {}:
                del structure[key]
        else:
            if not key.endswith('.py'):
                del structure[key]


code = """
\"\"\"
this is a module
...
\"\"\"
const = {1,2,3}
import os

class fooClass:
    '''this is a class'''

    def __init__(self, x):
        '''initialization.'''
        self.x = x

    def print(self):
        print(self.x)

large_var = {
    1: 2,
    2: 3,
    3: 4,
    4: 5,
    5: 6,
    6: 7,
    7: 8,
    8: 9,
    9: 10,
    10: 11,
    11: 12,
    12: 13,
    13: 14,
    14: 15,
    15: 16,
    16: 17,
    17: 18,
    18: 19,
    19: 20,
    20: 21,
}

def test():
    a = fooClass(3)
    a.print()

"""


class CompressTransformer(cst.CSTTransformer):
    DESCRIPTION = str = 'Replaces function body with ...'
    replacement_string = '"__FUNC_BODY_REPLACEMENT_STRING__"'

    def __init__(self, keep_constant=True, keep_indent=False):
        self.keep_constant = keep_constant
        self.keep_indent = keep_indent

    def leave_Module(
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        new_body = [
            stmt
            for stmt in updated_node.body
            if m.matches(stmt, m.ClassDef())
            or m.matches(stmt, m.FunctionDef())
            or (
                self.keep_constant
                and m.matches(stmt, m.SimpleStatementLine())
                and m.matches(stmt.body[0], m.Assign())
            )
        ]
        return updated_node.with_changes(body=new_body)

    def leave_ClassDef(
        self,
        original_node: cst.ClassDef,
        updated_node: cst.ClassDef,
    ) -> cst.ClassDef:
        # Remove docstring in the class body
        new_body = [
            stmt
            for stmt in updated_node.body.body
            if not (
                m.matches(stmt, m.SimpleStatementLine())
                and m.matches(stmt.body[0], m.Expr())
                and m.matches(stmt.body[0].value, m.SimpleString())
            )
        ]
        return updated_node.with_changes(body=cst.IndentedBlock(body=new_body))

    def leave_FunctionDef(
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.CSTNode:
        if not self.keep_indent:
            # replace with unindented statement
            new_expr = cst.Expr(value=cst.SimpleString(value=self.replacement_string))
            new_body = cst.IndentedBlock((new_expr,))
            return updated_node.with_changes(body=new_body)
        # replace with indented statement
        # new_expr = [cst.Pass()]
        new_expr = [
            cst.Expr(value=cst.SimpleString(value=self.replacement_string)),
        ]
        return updated_node.with_changes(
            body=cst.IndentedBlock(body=[cst.SimpleStatementLine(body=new_expr)]),
        )


def extract_code_blocks(text):
    pattern = r'```\n(.*?)\n```'
    matches = re.findall(pattern, text, re.DOTALL)
    if len(matches) == 0:
        if '```' in text:
            # handle the case where the code block is not complete
            return [text.split('```', 1)[-1].strip()]
    return matches


def extract_locs_for_files(locs, file_names, keep_old_order=False):
    if keep_old_order:
        results = {fn: [] for fn in file_names}
    else:
        results = {}  # dict is insertion ordered
    current_file_name = None
    for loc in locs:
        for line in loc.splitlines():
            if line.strip().endswith('.py'):
                current_file_name = line.strip()
            elif line.strip() and any(
                line.startswith(w) for w in ['line:', 'function:', 'class:', 'variable:']
            ):
                if current_file_name in file_names:
                    if current_file_name not in results:
                        results[current_file_name] = []
                    results[current_file_name].append(line)
                else:
                    pass

    for file_name in file_names:
        if file_name not in results:  # guard for new order case
            results[file_name] = []

    return {fn: ['\n'.join(results[fn])] for fn in results.keys()}


def remove_lines(raw_code, remove_line_intervals):
    # TODO: speed up this function
    # remove_line_intervals.sort()

    # Remove lines
    new_code = ''
    for i, line in enumerate(raw_code.splitlines()):
        # intervals are one-based
        if not any(start <= i + 1 <= end for start, end in remove_line_intervals):
            new_code += line + '\n'
        if any(start == i + 1 for start, _ in remove_line_intervals):
            new_code += '...\n'
    return new_code


def compress_assign_stmts(raw_code, total_lines=30, prefix_lines=10, suffix_lines=10):
    try:
        tree = cst.parse_module(raw_code)
    except Exception as e:
        print(e.__class__.__name__, e)
        return raw_code

    wrapper = cst.metadata.MetadataWrapper(tree)
    visitor = GlobalVariableVisitor()
    wrapper.visit(visitor)

    remove_line_intervals = []
    for stmt in visitor.assigns:
        if stmt[2].line - stmt[1].line > total_lines:
            remove_line_intervals.append(
                (stmt[1].line + prefix_lines, stmt[2].line - suffix_lines),
            )
    return remove_lines(raw_code, remove_line_intervals)


def get_skeleton(
    raw_code,
    keep_constant: bool = True,
    keep_indent: bool = False,
    compress_assign: bool = False,
    total_lines=30,
    prefix_lines=10,
    suffix_lines=10,
):
    try:
        tree = cst.parse_module(raw_code)
    except:
        return raw_code

    transformer = CompressTransformer(keep_constant=keep_constant, keep_indent=True)
    modified_tree = tree.visit(transformer)
    code = modified_tree.code

    if compress_assign:
        code = compress_assign_stmts(
            code,
            total_lines=total_lines,
            prefix_lines=prefix_lines,
            suffix_lines=suffix_lines,
        )

    if keep_indent:
        code = code.replace(CompressTransformer.replacement_string + '\n', '...\n')
        code = code.replace(CompressTransformer.replacement_string, '...\n')
    else:
        pattern = f'\\n[ \\t]*{CompressTransformer.replacement_string}'
        replacement = '\n...'
        code = re.sub(pattern, replacement, code)

    return code


def correct_file_paths(model_found_files, files):
    found_files = []
    if model_found_files:
        for model_file in model_found_files:
            for file_content in files:
                file = file_content[0]
                if model_file == file:
                    found_files.append(file)
        return found_files
    return []


def get_repo_files(structure, filepaths: list[str]):
    files, classes, functions = get_full_file_paths_and_classes_and_functions(structure)
    file_contents = dict()
    for filepath in filepaths:
        content = None

        for file_content in files:
            if file_content[0] == filepath:
                if(len(file_content[1]) > 0 and file_content[1][0].endswith('\n')):
                    content = "".join(file_content[1])
                else:
                    content = "\n".join(file_content[1])
                file_contents[filepath] = content
                break

        # assert content is not None, "file not found"
    return file_contents


def get_full_file_paths_and_classes_and_functions(structure, current_path=''):
    """
    Recursively retrieve all file paths, classes, and functions within a directory structure.

    Arguments:
    structure -- a dictionary representing the directory structure
    current_path -- the path accumulated so far, used during recursion (default="")

    Returns:
    A tuple containing:
    - files: list of full file paths
    - classes: list of class details with file paths
    - functions: list of function details with file paths
    """
    files = []
    classes = []
    functions = []
    for name, content in structure.items():
        if isinstance(content, dict):
            if (
                (
                    'functions' not in content.keys()
                    and 'classes' not in content.keys()
                    and 'text' not in content.keys()
                )
                or not len(content.keys()) == 3
                or (
                    isinstance(content.get('text', []), dict)
                    or isinstance(content.get('functions', []), dict)
                    or isinstance(content.get('classes', []), dict)
                )
            ):
                # or guards against case where functions and classes are somehow part of the structure.
                next_path = f'{current_path}/{name}' if current_path else name
                (
                    sub_files,
                    sub_classes,
                    sub_functions,
                ) = get_full_file_paths_and_classes_and_functions(content, next_path)
                files.extend(sub_files)
                classes.extend(sub_classes)
                functions.extend(sub_functions)
            else:
                next_path = f'{current_path}/{name}' if current_path else name
                files.append((next_path, content.get('text', [])))
                if content.get('text', []) == []:
                    continue
                if 'classes' in content:
                    for clazz in content['classes']:
                        classes.append(
                            {
                                'file': next_path,
                                'name': clazz['name'],
                                'start_line': clazz['start_line'],
                                'end_line': clazz['end_line'],
                                'methods': [
                                    {
                                        'name': method['name'],
                                        'start_line': method['start_line'],
                                        'end_line': method['end_line'],
                                    }
                                    for method in clazz.get('methods', [])
                                ],
                            },
                        )
                if 'functions' in content:
                    for function in content['functions']:
                        try:
                            function['file'] = next_path
                        except TypeError:
                            continue
                        functions.append(function)
        else:
            next_path = f'{current_path}/{name}' if current_path else name
            files.append(next_path)
    return files, classes, functions


def construct_topn_file_context(
    file_to_locs,
    pred_files,
    file_contents,
    structure,
    context_window: int,
    loc_interval: bool = True,
    fine_grain_loc_only: bool = False,
    add_space: bool = False,
    sticky_scroll: bool = False,
    no_line_number: bool = True,
):
    """Concatenate provided locations to form a context.

    loc: {"file_name_1": ["loc_str_1"], ...}
    """
    file_loc_intervals = dict()
    topn_content = ''

    for pred_file, locs in file_to_locs.items():
        try:
            content = file_contents[pred_file]
        except KeyError:
            import ipdb

            ipdb.set_trace()
        line_locs, context_intervals = transfer_arb_locs_to_locs(
            locs,
            structure,
            pred_file,
            context_window,
            loc_interval,
            fine_grain_loc_only,
            file_content=file_contents[pred_file] if pred_file in file_contents else '',
        )

        if len(line_locs) > 0:
            # Note that if no location is predicted, we exclude this file.
            file_loc_content = line_wrap_content(
                content,
                context_intervals,
                add_space=add_space,
                no_line_number=no_line_number,
                sticky_scroll=sticky_scroll,
            )
            topn_content += f'### {pred_file}\n{file_loc_content}\n\n\n'
            file_loc_intervals[pred_file] = context_intervals

    return topn_content, file_loc_intervals


def transfer_arb_locs_to_locs(
    locs,
    structure,
    pred_file,
    context_window=10,
    loc_interval=False,
    fine_grain_only=False,
    remove_line=False,
    file_content='',
    verbose=False,
) -> tuple[list, list]:
    if structure is None:
        class_info, function_names, file_lines = parse_python_file('', file_content)
        structure = {}
        structure[pred_file] = {
            'classes': class_info,
            'functions': function_names,
            'text': file_lines,
        }

    files, classes, functions = get_full_file_paths_and_classes_and_functions(structure)

    line_loc = []
    if isinstance(locs, str):
        # if its a single loc
        locs = [locs]
    # TODO: parse it in advance
    global_vars = parse_global_var_from_code(file_content)
    unrecognized_locs = []

    for model_pred_locs in locs:
        current_class_name = ''
        for loc in model_pred_locs.splitlines():
            # handle cases like "class: MyClass.my_method"
            if loc.startswith('class: ') and '.' not in loc:
                loc = loc[len('class: ') :].strip()
                relevant_class = [
                    clazz
                    for clazz in classes
                    if clazz['file'] == pred_file and clazz['name'] == loc
                ]

                if len(relevant_class) == 0:
                    unrecognized_locs.append(loc)
                else:
                    line_loc.append(
                        (relevant_class[0]['start_line'], relevant_class[0]['end_line']),
                    )
                    current_class_name = loc

            elif loc.startswith('function: ') or '.' in loc:
                full_loc = loc
                loc = loc.split(':', 1)[-1].strip()

                if '.' in loc:
                    # assume its a method within a class
                    method_name = loc.split('.')[1]
                    class_name = loc.split('.')[0]

                    relevant_class = [
                        clazz
                        for clazz in classes
                        if clazz['file'] == pred_file and clazz['name'] == class_name
                    ]
                    if len(relevant_class) == 0:
                        unrecognized_locs.append(loc)
                    else:
                        relevant_method = [
                            method
                            for method in relevant_class[0]['methods']
                            if method['name'] == method_name
                        ]
                        if len(relevant_method) == 0:
                            unrecognized_locs.append(loc)
                        else:
                            line_loc.append(
                                (
                                    relevant_method[0]['start_line'],
                                    relevant_method[0]['end_line'],
                                ),
                            )

                else:
                    relevant_function = [
                        function
                        for function in functions
                        if function['file'] == pred_file and function['name'] == loc
                    ]
                    if len(relevant_function) == 0:
                        if current_class_name != '':
                            # check if its a method
                            relevant_class = [
                                clazz
                                for clazz in classes
                                if clazz['file'] == pred_file
                                and clazz['name'] == current_class_name
                            ]
                            relevant_method = [
                                method
                                for method in relevant_class[0]['methods']
                                if method['name'] == loc
                            ]
                            if len(relevant_method) == 0:
                                unrecognized_locs.append(loc)
                            else:
                                line_loc.append(
                                    (
                                        relevant_method[0]['start_line'],
                                        relevant_method[0]['end_line'],
                                    ),
                                )
                        else:
                            # look for it in any class
                            relevant_method = []
                            for clazz in classes:
                                if clazz['file'] == pred_file:
                                    relevant_method.extend(
                                        [
                                            method
                                            for method in clazz['methods']
                                            if method['name'] == loc
                                        ],
                                    )

                            if len(relevant_method) == 1:
                                line_loc.append(
                                    (
                                        relevant_method[0]['start_line'],
                                        relevant_method[0]['end_line'],
                                    ),
                                )
                    else:
                        line_loc.append(
                            (
                                relevant_function[0]['start_line'],
                                relevant_function[0]['end_line'],
                            ),
                        )
            elif loc.startswith('line: '):
                if remove_line:
                    # TODO: can recover the corresponding function instead of throwing it away
                    continue
                loc = loc[len('line: ') :].strip().split()[0]
                try:
                    # line_loc.append(int(loc))
                    line_loc.append((int(loc), int(loc)))
                except:
                    continue
            elif loc.startswith('variable:'):
                vars = loc[len('variable:') :].strip().split()
                for v in vars:
                    if v in global_vars:
                        line_loc.append(
                            (global_vars[v]['start_line'], global_vars[v]['end_line']),
                        )
            else:
                if loc.strip():
                    unrecognized_locs.append(loc)
                # assert False

    # Fine-grained-only loc: Remove intervals that are supersets of another.
    if fine_grain_only:
        filtered_line_loc = []
        for st, en in line_loc:
            if filtered_line_loc:
                last_st, last_en = filtered_line_loc[-1]
                # If the current interval is a more fine-grained loc, remove the superset.
                if last_st <= st and en <= last_en:
                    filtered_line_loc.pop()
            filtered_line_loc.append((st, en))
        line_loc = filtered_line_loc

    # compute max min
    # TODO: think of strategies to do bunched up lines
    # TODO: e.g., we can have multiple code segments (right now, its just one)

    for file_content in files:
        if file_content[0] == pred_file:
            content = file_content[1]
            break

    if len(line_loc) == 0:
        return [], []

    # max_line = min(max(line_loc) + context_window, len(content))
    # min_line = max(min(line_loc) - context_window, 0)
    #
    # return line_loc, max_line, min_line

    if verbose:
        print('Unrecognized locs:')
        for loc in unrecognized_locs:
            print(loc)

    # compute overlapping locations instead
    if loc_interval:
        contextual_line_loc = []
        for loc in line_loc:
            # Clip the context window to the beginning and end of the file
            max_line = max(min(loc[1] + context_window, len(content)), 0)
            min_line = min(max(loc[0] - context_window, 0), len(content))
            contextual_line_loc.append((min_line, max_line))

        return line_loc, merge_intervals(contextual_line_loc)
    # defaulting to max min
    max_line = min(max([loc[1] for loc in line_loc]) + context_window, len(content))
    min_line = max(min([loc[0] for loc in line_loc]) - context_window, 0)

    return line_loc, [(min_line, max_line)]


def line_wrap_content(
    content: str,
    context_intervals=None,
    add_space=False,
    no_line_number=False,
    sticky_scroll=False,
):
    """add n| to each line, where n increases"""

    def is_scope(line):
        # TODO: this might not be precise, can improve with syntax parsing
        return line.startswith('class ') or line.strip().startswith('def ')

    lines = content.split('\n')
    new_lines = []
    if context_intervals is None or context_intervals == []:
        context_intervals = [(0, len(lines))]

    prev_scopes = []
    line_format = '{line}'
    if not no_line_number:
        line_format = '{line_number}|{line}' if not add_space else '{line_number}| {line} '
    for interval in context_intervals:
        min_line, max_line = interval

        if min_line != 0:
            new_lines.append('...')

        scopes = []
        for i, line in enumerate(lines):
            if sticky_scroll:
                # add current line to scope if necessary
                if is_scope(line):
                    indent_level = len(line) - len(line.lstrip())
                    while scopes and scopes[-1]['indent_level'] >= indent_level:
                        scopes.pop()
                    scopes.append(
                        {'line': line, 'line_number': i, 'indent_level': indent_level},
                    )

            if min_line != -1 and i < min_line - 1:
                continue
            if sticky_scroll and i == min_line - 1:
                # add scope lines
                last_scope_line = None
                for j, scope_line in enumerate(scopes):
                    # don't repeat previous scopes
                    if (
                        len(prev_scopes) > j
                        and prev_scopes[j]['line_number'] == scope_line['line_number']
                    ):
                        continue
                    # don't repeat current line
                    if i == scope_line['line_number']:
                        continue
                    new_lines.append(
                        line_format.format(
                            line_number=scope_line['line_number'] + 1,
                            line=scope_line['line'],
                        ),
                    )
                    last_scope_line = scope_line['line_number']
                if last_scope_line is not None and last_scope_line < i - 1:
                    new_lines.append('...')

            new_lines.append(line_format.format(line_number=i + 1, line=line))
            if max_line != -1 and i >= max_line - 1:
                break
        prev_scopes = scopes

    if max_line != len(lines):
        new_lines.append('...')

    return '\n'.join(new_lines)


def merge_intervals(intervals):
    # intervals inclusive
    if not intervals:
        return []

    # Sort the intervals based on the starting value of each tuple
    intervals.sort(key=lambda interval: interval[0])

    merged_intervals = [intervals[0]]

    for current in intervals[1:]:
        last = merged_intervals[-1]

        # Check if there is overlap
        if current[0] <= last[1]:
            # If there is overlap, merge the intervals
            merged_intervals[-1] = (last[0], max(last[1], current[1]))
        else:
            # If there is no overlap, just add the current interval to the result list
            merged_intervals.append(current)

    return merged_intervals


class GlobalVariableVisitor_parse(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)

    def __init__(self):
        self.global_assigns = []

    def leave_Module(self, original_node: cst.Module) -> list:
        assigns = []
        for stmt in original_node.body:
            if m.matches(stmt, m.SimpleStatementLine()) and m.matches(
                stmt.body[0],
                m.Assign(),
            ):
                start_pos = self.get_metadata(cst.metadata.PositionProvider, stmt).start
                end_pos = self.get_metadata(cst.metadata.PositionProvider, stmt).end
                assigns.append([stmt, start_pos, end_pos])
        self.global_assigns.extend(assigns)


class GlobalVariableVisitor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (cst.metadata.PositionProvider,)

    def __init__(self):
        self.assigns = []

    def leave_Assign(self, original_node: cst.Module) -> list:
        stmt = original_node
        start_pos = self.get_metadata(cst.metadata.PositionProvider, stmt).start
        end_pos = self.get_metadata(cst.metadata.PositionProvider, stmt).end
        self.assigns.append([stmt, start_pos, end_pos])


def parse_global_var_from_code(file_content: str) -> dict[str, dict]:
    """Parse global variables."""
    try:
        tree = cst.parse_module(file_content)
    except:
        return file_content

    wrapper = cst.metadata.MetadataWrapper(tree)
    visitor = GlobalVariableVisitor_parse()
    wrapper.visit(visitor)

    global_assigns = {}
    for assign_stmt, start_pos, end_pos in visitor.global_assigns:
        for t in assign_stmt.body:
            try:
                targets = [t.targets[0].target.value]
            except:
                try:
                    targets = t.targets[0].target.elements
                    targets = [x.value.value for x in targets]
                except:
                    targets = []
            for target_var in targets:
                global_assigns[target_var] = {
                    'start_line': start_pos.line,
                    'end_line': end_pos.line,
                }
    return global_assigns


def parse_python_file(file_path, file_content=None):
    """Parse a Python file to extract class and function definitions with their line numbers.
    :param file_path: Path to the Python file.
    :return: Class names, function names, and file contents
    """
    if file_content is None:
        try:
            with open(file_path) as file:
                file_content = file.read()
                parsed_data = ast.parse(file_content)
        except Exception as e:  # Catch all types of exceptions
            print(f'Error in file {file_path}: {e}')
            return [], [], ''
    else:
        try:
            parsed_data = ast.parse(file_content)
        except Exception as e:  # Catch all types of exceptions
            print(f'Error in file {file_path}: {e}')
            return [], [], ''

    class_info = []
    function_names = []
    class_methods = set()

    for node in ast.walk(parsed_data):
        if isinstance(node, ast.ClassDef):
            methods = []
            for n in node.body:
                if isinstance(n, ast.FunctionDef):
                    methods.append(
                        {
                            'name': n.name,
                            'start_line': n.lineno,
                            'end_line': n.end_lineno,
                            'text': file_content.splitlines()[n.lineno - 1 : n.end_lineno],
                        },
                    )
                    class_methods.add(n.name)
            class_info.append(
                {
                    'name': node.name,
                    'start_line': node.lineno,
                    'end_line': node.end_lineno,
                    'text': file_content.splitlines()[node.lineno - 1 : node.end_lineno],
                    'methods': methods,
                },
            )
        elif isinstance(node, ast.FunctionDef) and not isinstance(
            node,
            ast.AsyncFunctionDef,
        ):
            if node.name not in class_methods:
                function_names.append(
                    {
                        'name': node.name,
                        'start_line': node.lineno,
                        'end_line': node.end_lineno,
                        'text': file_content.splitlines()[node.lineno - 1 : node.end_lineno],
                    },
                )

    return class_info, function_names, file_content.splitlines()


def parse_patch(patch):
    """
    Parse a git patch into a structured format.

    Parameters:
        patch (str): The git patch as a string.

    Returns:
        list: A list of dictionaries representing the file changes and hunks.
    """
    file_changes = []
    current_file = None
    current_hunk = None
    deleted_lines = 0

    patch_lines = patch.split('\n')
    for line in patch_lines:
        if line.startswith('diff --git'):
            # Reset for new files
            if current_file:
                file_changes.append(current_file)
            current_file = {'file': '', 'hunks': []}
        elif line.startswith('--- a/'):
            pass
        elif line.startswith('+++ b/'):
            if current_file is not None:
                current_file['file'] = line[6:]
        elif line.startswith('@@ '):
            if current_file is not None:
                match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
                if match:
                    current_hunk = {'start_line': int(match.group(2)), 'changes': []}
                    current_file['hunks'].append(current_hunk)
                    deleted_lines = 0
                    added_lines = 0
        elif line.startswith('+') or line.startswith('-'):
            if current_hunk is not None:
                change_type = 'add' if line.startswith('+') else 'delete'
                if change_type == 'delete':
                    deleted_lines += 1
                    current_hunk['changes'].append(
                        {
                            'type': change_type,
                            'content': line[1:].strip(),
                            'line': current_hunk['start_line'] - added_lines,
                        },
                    )
                    current_hunk['start_line'] += 1
                else:
                    added_lines += 1
                    current_hunk['changes'].append(
                        {
                            'type': change_type,
                            'content': line[1:].strip(),
                            'line': current_hunk['start_line'] - deleted_lines,
                        },
                    )
                    current_hunk['start_line'] += 1
        else:
            if current_hunk is not None:
                current_hunk['start_line'] += 1

    if current_file:
        file_changes.append(current_file)

    return file_changes


def parse_patch_wo_strip(patch):
    """
    Parse a git patch into a structured format. the format is without strip in content. make the content more readable.

    Parameters:
        patch (str): The git patch as a string.

    Returns:
        list: A list of dictionaries representing the file changes and hunks.
    """
    file_changes = []
    current_file = None
    current_hunk = None
    deleted_lines = 0

    patch_lines = patch.split('\n')
    for line in patch_lines:
        if line.startswith('diff --git'):
            # Reset for new files
            if current_file:
                file_changes.append(current_file)
            current_file = {'file': '', 'hunks': []}
        elif line.startswith('--- a/'):
            pass
        elif line.startswith('+++ b/'):
            if current_file is not None:
                current_file['file'] = line[6:]
        elif line.startswith('@@ '):
            if current_file is not None:
                match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
                if match:
                    current_hunk = {'start_line': int(match.group(2)), 'changes': []}
                    current_file['hunks'].append(current_hunk)
                    deleted_lines = 0
                    added_lines = 0
        elif line.startswith('+') or line.startswith('-'):
            if current_hunk is not None:
                change_type = 'add' if line.startswith('+') else 'delete'
                if change_type == 'delete':
                    deleted_lines += 1
                    current_hunk['changes'].append(
                        {
                            'type': change_type,
                            'content': line[1:],
                            'line': current_hunk['start_line'] - added_lines,
                        },
                    )
                    current_hunk['start_line'] += 1
                else:
                    added_lines += 1
                    current_hunk['changes'].append(
                        {
                            'type': change_type,
                            'content': line[1:],
                            'line': current_hunk['start_line'] - deleted_lines,
                        },
                    )
                    current_hunk['start_line'] += 1
        else:
            if current_hunk is not None:
                current_hunk['start_line'] += 1

    if current_file:
        file_changes.append(current_file)

    return file_changes


def show_project_structure(structure, spacing=0) -> str:
    """pprint the project structure"""

    pp_string = ''

    for key, value in structure.items():
        if '.' in key and '.py' not in key:
            continue  # skip none python files

        # TODO: maybe we should skip the test files...
        if key.startswith('test'):
            continue  # skip the test files as well...

        if '.' in key:
            pp_string += ' ' * spacing + str(key) + '\n'
        else:
            pp_string += ' ' * spacing + str(key) + '/' + '\n'
        if 'classes' not in value:
            pp_string += show_project_structure(value, spacing + 4)

    return pp_string


def get_full_file_paths_and_classes_and_functions(structure, current_path=""):
    """
    Recursively retrieve all file paths, classes, and functions within a directory structure.

    Arguments:
    structure -- a dictionary representing the directory structure
    current_path -- the path accumulated so far, used during recursion (default="")

    Returns:
    A tuple containing:
    - files: list of full file paths
    - classes: list of class details with file paths
    - functions: list of function details with file paths
    """
    files = []
    classes = []
    functions = []
    for name, content in structure.items():
        if isinstance(content, dict):
            if (
                not "functions" in content.keys()
                and not "classes" in content.keys()
                and not "text" in content.keys()
            ) or not len(content.keys()) == 3 :
                # or guards against case where functions and classes are somehow part of the structure.
                next_path = f"{current_path}/{name}" if current_path else name
                (
                    sub_files,
                    sub_classes,
                    sub_functions,
                ) = get_full_file_paths_and_classes_and_functions(content, next_path)
                files.extend(sub_files)
                classes.extend(sub_classes)
                functions.extend(sub_functions)
            elif (isinstance(content.get("text",[]), dict) or isinstance(content.get("functions",[]), dict) or isinstance(content.get("classes",[]), dict)):
                # or guards against case where functions and classes are somehow part of the structure.
                next_path = f"{current_path}/{name}" if current_path else name
                (
                    sub_files,
                    sub_classes,
                    sub_functions,
                ) = get_full_file_paths_and_classes_and_functions(content, next_path)
                files.extend(sub_files)
                classes.extend(sub_classes)
                functions.extend(sub_functions)                
            else:
                next_path = f"{current_path}/{name}" if current_path else name
                files.append((next_path, content.get("text",[])))
                if content.get("text",[]) == []:
                    continue
                if "classes" in content:
                    for clazz in content["classes"]:
                        classes.append(
                            {
                                "file": next_path,
                                "name": clazz["name"],
                                "start_line": clazz["start_line"],
                                "end_line": clazz["end_line"],
                                "methods": [
                                    {
                                        "name": method["name"],
                                        "start_line": method["start_line"],
                                        "end_line": method["end_line"],
                                    }
                                    for method in clazz.get("methods", [])
                                ],
                            }
                        )
                if "functions" in content:
                    for function in content["functions"]:
                        try:
                            function["file"] = next_path
                        except TypeError:
                            continue
                        functions.append(function)
        else:
            next_path = f"{current_path}/{name}" if current_path else name
            files.append(next_path)
    return files, classes, functions


def show_project_structure(structure, spacing=0, test_flag=False) -> str:
    """pprint the project structure"""

    pp_string = ""

    for key, value in structure.items():
        if "." in key and ".py" not in key:
            continue  # skip none python files
        
        # TODO: maybe we should skip the test files...
        if(not test_flag):
            if key.startswith("test"):
                continue  # skip the test files as well...

        if "." in key:
            pp_string += " " * spacing + str(key) + "\n"
        else:
            pp_string += " " * spacing + str(key) + "/" + "\n"
        if "classes" not in value:
            pp_string += show_project_structure(value, spacing + 4, test_flag)

    return pp_string

def process_instance_data(json_obj):
    """
    Process the json object and extract required fields.

    Args:
    - json_obj (dict): The parsed JSON object from a line in the JSONL file.

    Returns:
    - dict: A dictionary containing 'instance_id' and other relevant fields.
    """
    instance_id = json_obj.get('instance_id')
    if not instance_id:
        print("Warning: 'instance_id' not found")
        return None

    return {
        'instance_id': instance_id,
        'patch': json_obj.get('patch'),
        'test_patch': json_obj.get('test_patch'),
        'parsed_patch': json_obj.get('parsed_patch', {}),
        'parsed_test_patch': json_obj.get('parsed_test_patch', {}),
        'problem_statement': json_obj.get('problem_statement', {}),
        'hints_text': json_obj.get('hints_text', {}),
    }


def read_instance_ids_from_jsonl(
    jsonl_file_path,
    repostructure_dir,
    selected_num=None,
    selected_ids=None,
):
    """
    Reads a .jsonl file and processes each line to extract relevant fields.

    Args:
    - jsonl_file_path (str): The path to the .jsonl file.
    - selected_num (int, optional): The number of instances to randomly select. If None, return all.
    - selected_ids (list, optional): The list of instance ids to select. If None, return all.

    Returns:
    - list: A list of dictionaries containing 'instance_id' and other fields.
    """
    instances = []

    # Open and read the .jsonl file
    with open(jsonl_file_path, encoding='utf-8') as jsonl_file:
        for line in jsonl_file:
            try:
                # Parse the JSON object from the current line
                json_obj = json.loads(line.strip())

                # Process the instance data
                instance_data = json_obj
                # instance_data = process_instance_data(json_obj)

                instance_id = instance_data.get('instance_id')
                structure_flag = check_structure_exits(instance_id, repostructure_dir)

                # Store valid instance data
                if instance_data and structure_flag is True:
                    if selected_ids is not None and instance_id not in selected_ids:
                        continue
                    instances.append(instance_data)
                # If selected_num is specified, randomly sample the specified number of instances
                if selected_num is not None and selected_num == len(instances):
                    return instances
            except json.JSONDecodeError:
                print(f'Error: Invalid JSON format in line: {line.strip()}')
            except Exception as e:
                print(f'Error processing line: {line.strip()}, Error: {e}')

    return instances


def read_instance_ids_from_json(
    json_file_path,
    repostructure_dir,
    selected_num=None,
    selected_ids=None,
):
    """
    Reads a .jsonl file and processes each line to extract relevant fields.

    Args:
    - json_file_path (str): The path to the .jsonl file.
    - selected_num (int, optional): The number of instances to randomly select. If None, return all.
    - selected_ids (list, optional): The list of instance ids to select. If None, return all.

    Returns:
    - list: A list of dictionaries containing 'instance_id' and other fields.
    """
    instances = []

    # Open and read the .jsonl file
    with open(json_file_path, encoding='utf-8') as json_file:
        # Parse the JSON object from the current line
        json_objs = json.load(json_file)
        for _, json_obj in json_objs.items():
            # Process the instance data
            instance_data = json_obj
            # instance_data = process_instance_data(json_obj)

            instance_id = instance_data.get('instance_id')
            structure_flag = check_structure_exits(instance_id, repostructure_dir)

            # Store valid instance data
            if instance_data and structure_flag is True:
                if selected_ids is not None and instance_id not in selected_ids:
                    continue
                instances.append(instance_data)
            # If selected_num is specified, randomly sample the specified number of instances
            if selected_num is not None and selected_num == len(instances):
                return instances

    return instances


def check_structure_exits(instance_id, json_dir_path):
    # Construct the file path of the corresponding <instance_id>.json file
    instance_file_path = os.path.join(json_dir_path, f'{instance_id}.json')
    if os.path.exists(instance_file_path):
        return True
    return False


def search_instance_id_and_extract_structure(instance_id, json_dir_path, flag=0):
    """
    Searches for a file corresponding to the 'instance_id' and extracts the 'structure' field.

    Args:
    - instance_id (str): The instance ID to search for.
    - json_dir_path (str): The directory where the <instance_id>.json files are located.
    - flag (int): Controls the logging level (0: no log, 1: info, 2: debug).

    Returns:
    - dict: The structure data if found, else None.
    """
    # Set up logging configuration
    if flag == 0:
        logging.disable(logging.CRITICAL)  # Disable logging entirely if flag is 0
    elif flag == 1:
        logging.basicConfig(level=logging.INFO)  # Log messages at INFO level
    else:
        logging.basicConfig(level=logging.DEBUG)  # Log messages at DEBUG level

    # Construct the file path of the corresponding <instance_id>.json file
    instance_file_path = os.path.join(json_dir_path, f'{instance_id}.json')

    # Check if the file exists
    if os.path.exists(instance_file_path):
        # Open the <instance_id>.json file and extract the 'structure' key
        with open(instance_file_path, encoding='utf-8') as instance_file:
            try:
                instance_data = json.load(instance_file)
                structure = instance_data.get('structure')

                if structure:
                    logging.info(f'Structure for instance_id {instance_id} is found.')
                    return structure
                logging.warning(f"'structure' not found in file {instance_file_path}")
            except json.JSONDecodeError:
                logging.exception(f'Error decoding JSON in file {instance_file_path}')
    else:
        # If the file does not exist, log a warning message
        logging.warning(f'File not found for instance_id {instance_id}: {instance_file_path}')

    return None


def get_relevant_files(instance_data, test_flag=False):
    if test_flag:
        key = 'parsed_test_patch'
    else:
        key = 'parsed_patch'
    # Process parsed_patch to extract files
    if isinstance(instance_data[key], list):
        found_files = []
        for parsed_item in instance_data[key]:
            if isinstance(parsed_item, dict):
                files = parsed_item.get('file')
                if files:
                    found_files.append(files)
        return found_files
    print(f"Warning: 'parsed_patch' is not a list in instance data: {instance_data}")
    return []


def get_file_text(file_paths, struture):
    file_text_dict = {}
    for file_path in file_paths:
        file_structure = extract_file_info_from_structure(struture, file_path)
        if file_structure:
            file_text_dict[file_path] = file_structure.get('text', '')

    return file_text_dict


def extract_file_info_from_structure(structure, file_path):
    """
    Extract file information from a nested structure based on the given file path.

    Args:
        structure (dict): The hierarchical file structure.
        file_path (str): The path to the file (e.g., 'server/apps/club/views.py').

    Returns:
        dict or None: The file's information if found, else None.
    """
    path_components = file_path.split('/')
    current_structure = structure

    for component in path_components:
        if isinstance(current_structure, dict) and component in current_structure:
            current_structure = current_structure[component]
        else:
            # print(f"Warning: '{component}' not found in structure at this level.") # comment out this for clearer logs
            return None

    return current_structure


def find_keys_with_lines(structure, parent_keys=None):
    """
    Recursively search a dictionary or list for the first element containing both 'start_line' and 'end_line'.
    Also records the path to these elements and stops searching further once a match is found.

    Parameters:
        structure (dict or list): The dictionary or list to search through.
        parent_keys (list): The current path of parent keys to the current dictionary level.

    Returns:
        List of paths (list of keys) that point to the elements containing both 'start_line' and 'end_line'.
        Stops searching further once the first match is found at the current level.
    """
    if parent_keys is None:
        parent_keys = []

    results = []

    # If the structure is a dictionary, check for 'start_line' and 'end_line' at the current level
    if isinstance(structure, dict):
        if 'start_line' in structure and 'end_line' in structure:
            # If both 'start_line' and 'end_line' are found, add the path and stop further search
            results.append(parent_keys)
            return results  # Stop recursion here

        # Recursively process each key-value pair in the dictionary
        for key, sub_structure in structure.items():
            current_path = parent_keys + [key]
            results.extend(find_keys_with_lines(sub_structure, current_path))

    # If the structure is a list, iterate through the list and recursively process each item
    elif isinstance(structure, list):
        for idx, item in enumerate(structure):
            current_path = parent_keys + [idx]
            results.extend(find_keys_with_lines(item, current_path))

    # # Explicitly trigger garbage collection to clean up unused memory
    # gc.collect()

    return results


def find_affected_functions_locs(parsed_patch, structure):
    """
    Finds the affected functions and locs(specific lines) from parsed_patch and structure (function info).
    """
    affected_functions = {}
    affected_locs = {}

    for patch in parsed_patch:
        file = patch.get('file')
        hunks = patch.get('hunks', [])

        current_structure = extract_file_info_from_structure(structure, file)

        if current_structure:
            for hunk in hunks:
                for change in hunk.get('changes', []):
                    line_number = change.get('line')
                    find_line_flag = 0

                    # Find functions and classes affected by this line
                    classes_or_funcs = find_keys_with_lines(current_structure)

                    for class_or_func_path in classes_or_funcs:
                        # Directly access the class or function structure using the path
                        class_or_func_structure = current_structure
                        for path_segment in class_or_func_path:
                            class_or_func_structure = class_or_func_structure[path_segment]

                        # If class_or_func_structure has 'methods' keys, it is a class
                        if 'methods' in class_or_func_structure.keys():
                            class_structure = class_or_func_structure
                            funcs = find_keys_with_lines(class_or_func_structure['methods'])

                            for func_path in funcs:
                                func_structure = class_structure['methods']
                                for path_segment in func_path:
                                    func_structure = func_structure[path_segment]

                                if (
                                    func_structure['start_line']
                                    <= line_number
                                    <= func_structure['end_line']
                                ):
                                    find_line_flag = 1
                                    if file not in affected_functions:
                                        affected_functions[file] = []
                                        affected_locs[file] = []
                                    affected_functions[file].append(
                                        f'class: {class_structure["name"]}\nfunction: {class_structure["name"]}.{func_structure["name"]}',
                                    )
                                    affected_locs[file].append(
                                        f'class: {class_structure["name"]}\nfunction: {class_structure["name"]}.{func_structure["name"]}\nline: {line_number}',
                                    )
                        else:
                            func_structure = class_or_func_structure
                            if (
                                func_structure['start_line']
                                <= line_number
                                <= func_structure['end_line']
                            ):
                                find_line_flag = 1
                                if file not in affected_functions:
                                    affected_functions[file] = []
                                    affected_locs[file] = []
                                affected_functions[file].append(
                                    f'function: {func_structure["name"]}',
                                )
                                affected_locs[file].append(
                                    f'function: {func_structure["name"]}\nline: {line_number}',
                                )

                    # NOTE: Some modified lines are not in a class or func
                    if find_line_flag == 0:
                        if file not in affected_functions:
                            affected_functions[file] = []
                            affected_locs[file] = []
                        affected_functions[file].append('')
                        affected_locs[file].append(f'line: {line_number}')

    # Remove duplicate entries
    affected_functions = remove_duplicates(affected_functions)
    affected_locs = remove_duplicates(affected_locs)

    # # Perform one final garbage collection to clean up after all processing
    # gc.collect()

    return affected_functions, affected_locs


def generate_found_edit_locs(parsed_patch, structure):
    """
    Given the parsed_patch and structure, generate the output format with affected functions.
    """
    # Step 1: Extract function information from structure
    # function_info = extract_function_info_from_structure(structure)

    # Step 2: Find affected functions based on parsed_patch
    affected_functions, affected_locs = find_affected_functions_locs(parsed_patch, structure)

    # Prepare related locs (funcs)
    found_related_locs = []
    for file, functions in affected_functions.items():
        file_output = {file: functions}
        found_related_locs.append(file_output)

    # Prepare edit locs (funcs)
    found_edit_locs = []
    for file, functions in affected_locs.items():
        file_output = {file: functions}
        found_edit_locs.append(file_output)

    return found_related_locs, found_edit_locs


def correct_file_path_in_structure(file_name, structure):
    """
    Search for the correct file path in the structure, mainly checking first-level subdirectories

    Args:
        file_name (str): File name to search for
        structure (dict): Repository structure

    Returns:
        str: Correct file path if found, otherwise returns original file_name
    """
    # Search in current directory
    file_contents = get_repo_files(structure, [file_name])
    if file_contents != {}:
        return file_name

    # Only check first-level subdirectories
    for sub_dir in structure.keys():
        if isinstance(structure[sub_dir], dict):
            file_contents = get_repo_files(structure[sub_dir], [file_name])
            if file_contents != {}:
                return f'{sub_dir}/{file_name}'

    return file_name
