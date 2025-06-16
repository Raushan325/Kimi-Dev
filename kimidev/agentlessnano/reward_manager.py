import difflib
import json
import os
import re

import matplotlib.pyplot as plt
import numpy as np


REWARD_DICT = {
    'file_reward': 'pred_found_files',
    'func_reward': 'pred_found_related_locs',
    'line_reward': 'pred_found_edit_locs',
    'repair_reward': 'model_patch',
}


class RewardManager:
    def __init__(
        self,
        gt_data,
        raw_llmoutput_flag=False,
        repair_embedd_llm='Qwen/Qwen2.5-Coder-32B-Instruct',
        embedd_model_len=4096,
    ):
        self.gt_data = gt_data
        self.raw_llmoutput_flag = raw_llmoutput_flag

        if repair_embedd_llm is not None:
            from vllm import LLM

            self.llm_embedd = LLM(
                model=repair_embedd_llm,
                task='embed',
                max_model_len=embedd_model_len,
            )
        else:
            self.llm_embedd = None

    def _load_instance_data(self, instance_id):
        filename = instance_id.split('-')[0] + '-ds' + '.jsonl'
        filepath = os.path.join(self.data_folder, filename)

        with open(filepath, encoding='utf-8') as file:
            for line in file:
                try:
                    data = json.loads(line)
                    if data.get('instance_id') == instance_id:
                        return data
                except json.JSONDecodeError:
                    print(f'Error decoding JSON line in {filename}.')
        return None

    def calculate_reward(self, instance_id, llm_output, method='localize_file'):
        data = self.gt_data

        if method == 'file_reward':
            found_files = set(data.get('found_files', []))
            return self._calculate_reward_localize_file(found_files, llm_output)
        if method == 'func_reward':
            found_related_locs = data.get('found_related_locs', [])
            found_functions, found_classes = self._extract_functions_and_classes(found_related_locs)
            return self._calculate_reward_localize_func(found_functions, found_classes, llm_output)
        if method == 'line_reward':
            found_edit_locs = data.get('found_edit_locs', [])
            found_lines = self._extract_lines(found_edit_locs)
            return self._calculate_reward_localize_line_by_chunk(found_lines, llm_output)
        if method == 'repair_reward':
            gt_patch = data.get('gt_patch', [])
            if self.llm_embedd == None:
                return self._calculate_patch_similarity(gt_patch, llm_output)
            return self._calculate_reward_repair_diff(gt_patch, llm_output)
        raise ValueError(f'Unsupported method: {method}')

    def parse_function_components(self, func_str):
        class_name, func_name = None, None
        class_match = re.search(r'class:\s*([^\n]+)', func_str)
        func_match = re.search(r'function:\s*([^\n]+)', func_str)

        if class_match:
            class_name = class_match.group(1).strip()
        if func_match:
            full_func = func_match.group(1).strip()
            func_name = full_func
            if '.' in full_func:
                cls_part, func_part = full_func.split('.', 1)
                class_name = class_name or cls_part
        return class_name, func_name

    def _extract_functions_and_classes(self, found_related_locs):
        functions_by_file = {}
        classes_by_file = {}

        for loc in found_related_locs:
            for file, locs in loc.items():
                functions = set()
                classes = set()
                for loc in locs:
                    loc_parts = loc.split('\n')
                    for part in loc_parts:
                        class_name, func_name = self.parse_function_components(part)
                        if class_name:
                            classes.add(class_name)
                        if func_name:
                            functions.add(func_name)

                functions_by_file[file] = functions
                classes_by_file[file] = classes

        return functions_by_file, classes_by_file

    def _extract_lines(self, found_edit_locs):
        lines = {}

        for loc in found_edit_locs:
            for file, locs in loc.items():
                if file not in lines:
                    lines[file] = []

                for loc in locs:
                    loc_parts = loc.split('\n')
                    for part in loc_parts:
                        if part.startswith('line:'):
                            line_number = part.split(':')[1].strip()
                            numbers = re.findall(
                                r'\d+',
                                line_number,
                            )  # find all digits numbers in the line_number
                            lines[file].extend(numbers)
                        elif part.startswith('function:') or part.startswith('class:'):
                            continue

        return lines

    def _calculate_reward_localize_file(self, found_files, llm_output):
        # Assume llm_output is already a list of filenames (strings)
        if self.raw_llmoutput_flag:
            llm_files = set(file.strip() for file in llm_output if file.strip())
        else:
            llm_files = set(llm_output)
            
        match_count = len(found_files & llm_files)
        
        # 计算精确率和召回率
        precision = match_count / len(llm_files) if llm_files else 0
        recall = match_count / len(found_files) if found_files else 0
        
        # 计算F1分数
        if precision + recall > 0:
            f1 = 2 * precision * recall / (precision + recall)
        else:
            f1 = 0
            
        return f1

    def _calculate_reward_localize_func(self, found_functions, found_classes, llm_output):
        if self.raw_llmoutput_flag:
            llm_functions_by_file = {}
            llm_classes_by_file = {}

            current_file = None
            functions = set()
            classes = set()

            for line in llm_output:
                line = line.strip()
                if '/' in line and not (line.startswith('function:') or line.startswith('class:')):
                    if current_file:
                        llm_functions_by_file[current_file] = functions
                        llm_classes_by_file[current_file] = classes
                    current_file = line.strip()
                    functions = set()
                    classes = set()
                elif line.startswith('function:') or line.startswith('class:'):
                    class_name, func_name = self.parse_function_components(line)
                    if class_name:
                        classes.add(class_name)
                    if func_name:
                        functions.add(func_name)

            if current_file:
                llm_functions_by_file[current_file] = functions
                llm_classes_by_file[current_file] = classes
        else:
            if not isinstance(llm_output, list):
                llm_output = [llm_output]
            llm_functions_by_file, llm_classes_by_file = self._extract_functions_and_classes(
                llm_output,
            )

        total_gt_funcs = 0
        total_pred_funcs = 0
        for file in found_functions.keys():
            total_gt_funcs += len(found_functions.get(file, set())) + len(
                found_classes.get(file, set()),
            )
        for file in llm_functions_by_file.keys():
            total_pred_funcs += len(llm_functions_by_file.get(file, set())) + len(
                llm_classes_by_file.get(file, set()),
            )
        if total_pred_funcs > total_gt_funcs * 2 + 3:
            return 0

        total_matches = 0
        total_items = 0
        for file in found_functions.keys():
            matched_functions = found_functions.get(file, set()) & llm_functions_by_file.get(
                file,
                set(),
            )
            matched_classes = found_classes.get(file, set()) & llm_classes_by_file.get(file, set())

            total_matches += len(matched_functions) + len(matched_classes)
            total_items += len(found_functions[file]) + len(found_classes[file])

        return total_matches / total_items if total_items else 0

    def _calculate_reward_localize_line(self, found_lines, llm_output):
        if self.raw_llmoutput_flag:
            llm_lines = {}
            current_file = None
            for line in llm_output:
                line = line.strip()
                if line.startswith('line:'):
                    line_number = line.split(':')[1].strip()
                    if current_file not in llm_lines:
                        llm_lines[current_file] = []
                    llm_lines[current_file].append(line_number)
                elif line.endswith('.py'):
                    current_file = line.strip()
        else:
            if not isinstance(llm_output, list):
                llm_output = [llm_output]
            llm_lines = self._extract_lines(llm_output)

        total_matches = 0
        total_lines = 0
        for file, lines in found_lines.items():
            total_lines += len(lines)
            if file in llm_lines:
                total_matches += len(set(lines) & set(llm_lines[file]))

        return total_matches / total_lines if total_lines else 0

    def _calculate_reward_localize_line_by_chunk(self, found_lines, llm_output):
        if self.raw_llmoutput_flag:
            llm_lines = {}
            current_file = None
            for line in llm_output:
                line = line.strip()
                if line.startswith('line:'):
                    line_number = line.split(':')[1].strip()
                    if current_file not in llm_lines:
                        llm_lines[current_file] = []
                    llm_lines[current_file].append(line_number)
                elif line.endswith('.py'):
                    current_file = line.strip()
        else:
            if not isinstance(llm_output, list):
                llm_output = [llm_output]
            llm_lines = self._extract_lines(llm_output)

        total_gt_lines = 0
        total_pred_lines = 0
        total_reward = 0
        total_groups = 0

        def convert_to_int(lst):
            result = []
            for item in lst:
                try:
                    num = int(item)  # 尝试将字符串转换为整数
                    result.append(num)
                except ValueError:  # 如果转换失败，捕获ValueError异常并跳过
                    continue
            return result

        for file, lines in found_lines.items():
            total_gt_lines += len(lines)

        for k, v in llm_lines.items():
            total_pred_lines += len(v)

        if total_pred_lines > total_gt_lines * 2 + 5:
            return 0

        for file, lines in found_lines.items():
            lines_num = convert_to_int(lines)
            lines_num.sort()
            if file in llm_lines:
                pred_num = convert_to_int(llm_lines[file])
            else:
                pred_num = []

            # chunks
            group_loc_cnt = 0
            group_reward = 0

            for i, num in enumerate(lines_num):
                # 目前策略：大于80%的行匹配上算匹配
                flag = 0
                for j in range(num - 3, num + 4):
                    if j in pred_num:
                        flag = max(flag, 1 - 0.1 * abs(j - num))
                group_reward += flag
                group_loc_cnt += 1

                if (i == len(lines_num) - 1) or (lines_num[i + 1] - num > 2):
                    if group_reward / group_loc_cnt >= 0.8:
                        total_reward += group_reward / group_loc_cnt
                    total_groups += 1

        if total_groups > 0:
            total_reward = total_reward / total_groups
        else:
            total_reward = 0

        return total_reward

    def _calculate_reward_repair_diff(self, gt_patch, llm_output):
        pred_patch = llm_output
        outputs = self.llm_embedd.embed([pred_patch])
        pred_patch_embedd = outputs[0].outputs.embedding if outputs else []

        gt_outputs = self.llm_embedd.embed([gt_patch])
        gt_patch_embedd = gt_outputs[0].outputs.embedding if gt_outputs else []

        if len(pred_patch_embedd) > 0 and len(gt_patch_embedd) > 0:
            return self.cosine_similarity(pred_patch_embedd, gt_patch_embedd)
        return 0

    def _calculate_patch_similarity(self, gt_patch: str, model_patch: str) -> float:
        """
        计算两个字符串列表的平均相似度。

        参数:
            model_patch (str): model_patch 的字符串，按行分隔。
            gt_patch (str): gt_patch 的字符串，按行分隔。

        返回:
            float: 平均相似度。
        """
        model_search_lines, model_replace_lines, _ = self.extract_diff_changes(model_patch)
        gt_search_lines, gt_replace_lines, _ = self.extract_diff_changes(gt_patch)

        model_search_text = '\n'.join(model_search_lines)
        model_replace_text = '\n'.join(model_replace_lines)
        gt_search_text = '\n'.join(gt_search_lines)
        gt_replace_text = '\n'.join(gt_replace_lines)

        search_sim = difflib.SequenceMatcher(None, gt_search_text, model_search_text).ratio()
        replace_sim = difflib.SequenceMatcher(None, gt_replace_text, model_replace_text).ratio()

        return (search_sim + replace_sim) / 2

    def cosine_similarity(self, vec1, vec2):
        return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

    def extract_diff_changes(self, diff_text):
        search_lines = []
        replace_lines = []
        chunks = []
        current_chunk = []

        lines = diff_text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i]

            # 跳过文件头部的diff标记
            if line.startswith('---') or line.startswith('+++'):
                i += 1
                continue

            # 找到删除行
            if line.startswith('-'):
                search_lines.append(line[1:])
                current_chunk.append(line[1:])
                i += 1

            # 找到添加行
            elif line.startswith('+'):
                replace_lines.append(line[1:])
                current_chunk.append(line[1:])
                i += 1

            else:
                if len(current_chunk) > 0:
                    chunks.append(current_chunk)
                    current_chunk = []
                i += 1

        if len(current_chunk) > 0:
            chunks.append(current_chunk)

        return search_lines, replace_lines, chunks


def average_rewards(results_path, instance_ids=None):
    """
    Obtain the average reward of each type.
    If instance_ids are provided, it reads only those instances.
    Otherwise, it scans the results_path for all JSON or JSONL files.

    params:
        instance_ids: list of instance ids (optional)
        results_path: path to the results folder (folder containing .json or .jsonl files)
    """
    instance_rewards = {}

    # 如果没有提供 instance_ids，则自动查找所有 JSON 或 JSONL 文件
    if instance_ids is None:
        # 扫描 results_path 目录中的所有文件
        all_files = os.listdir(results_path)
        # 过滤出所有的 JSON 和 JSONL 文件
        instance_ids = [
            os.path.splitext(file)[0] for file in all_files if file.endswith(('.json', '.jsonl'))
        ]

    # 遍历所有 instance_ids
    for ins_id in instance_ids:
        # 构建对应的结果文件路径
        results_file = (
            os.path.join(results_path, f'{ins_id}.jsonl')
            if os.path.exists(os.path.join(results_path, f'{ins_id}.jsonl'))
            else os.path.join(results_path, f'{ins_id}.json')
        )

        if not os.path.exists(results_file):
            print(f'Results file for instance {ins_id} does not exist.')
            continue

        # 读取 JSONL 或 JSON 文件
        with open(results_file) as f:
            reward_types = set()
            if results_file.endswith('.jsonl'):
                results = [json.loads(line) for line in f]
                for result in results:
                    reward_types.update(result['rewards'].keys())  # 动态获取所有奖励类型

                # 存储每个奖励类型的平均值
                avg_rewards = {}
                for reward_type in reward_types:
                    reward_values = [result['rewards'].get(reward_type, 0) for result in results]
                    avg_rewards[f'avg_{reward_type}'] = (
                        np.mean(reward_values) if reward_values else 0
                    )
            else:  # 如果是 .json 文件
                results = json.load(f)
                reward_types.update(results['rewards'].keys())

                # 存储每个奖励类型的平均值
                avg_rewards = {}
                for reward_type in reward_types:
                    reward_values = results['rewards'].get(reward_type, 0)
                    avg_rewards[f'avg_{reward_type}'] = (
                        np.mean(reward_values) if reward_values else 0
                    )

            # 存储数据
            instance_rewards[ins_id] = avg_rewards

    return instance_rewards


def plot_reward_distribution(instance_rewards, reward_type, save_path, bins=20):
    """
    Plots the reward distribution as a bar plot and saves it as a PNG image.

    Parameters:
    - instance_rewards: A dictionary containing the rewards for each instance.
    - reward_type: The type of reward to plot ('avg_file_reward', 'avg_func_reward', etc.).
    - save_path: The path where the image will be saved.
    - bins: The number of bins for the histogram.
    """
    # Extract all reward values for the given reward_type
    all_rewards = []
    for rewards in instance_rewards.values():
        if reward_type in rewards:
            all_rewards.append(rewards[reward_type])

    # Create a figure for the plot
    plt.figure(figsize=(10, 5))

    # Calculate the histogram for the rewards
    counts, bins, _ = plt.hist(all_rewards, bins=bins, color='blue', alpha=0.7, edgecolor='black')

    # Normalize to probabilities
    total_instances = len(all_rewards)
    probs = counts / total_instances

    # Clear the previous plot and plot probabilities as a bar chart
    plt.clf()  # Clear previous figure
    plt.bar(
        bins[:-1],
        probs,
        width=np.diff(bins),
        color='blue',
        alpha=0.7,
        edgecolor='black',
        align='edge',
    )

    # Set labels and title
    plt.xlabel(f'{reward_type} Value')
    plt.ylabel('Probability')
    plt.title(f'Distribution of {reward_type} in {len(instance_rewards)} Instances')
    plt.grid(True)

    # Save the figure to the specified path
    save_path = os.path.join(save_path, f'{reward_type}_dist.png')
    plt.savefig(save_path, dpi=300)
    plt.close()


def calculate_rewards(
    instance_id,
    gt_results,
    generated_results,
    methods: list[str],
    add_format_rew=False,
):
    # if "repair_reward" in methods:
    #     reward_manager = RewardManager(gt_results, raw_llmoutput_flag=False, repair_embedd_llm="Qwen/Qwen2.5-Coder-32B-Instruct")
    # else:
    #     reward_manager = RewardManager(gt_results, raw_llmoutput_flag=False, repair_embedd_llm=None)
    reward_manager = RewardManager(gt_results, raw_llmoutput_flag=False, repair_embedd_llm=None)

    reward_dict = {}

    for method in methods:
        reward = reward_manager.calculate_reward(
            instance_id,
            generated_results[REWARD_DICT[method]],
            method=method,
        )
        reward_dict[method] = reward
        if add_format_rew:
            if len(generated_results[REWARD_DICT[method]]) == 0:
                format_reward = -1.0
            else:
                format_reward = 0.0
            reward_dict[f'format_{method}'] = format_reward

    return reward_dict


def rewards_dist_analysis(result_path):
    # result_path ="/home/wangshengjie/o3_project/Agentless/results/swe-gym-lite-r1-prompt-response-v3-filtered"

    instance_rewards = average_rewards(result_path)

    output_dir = os.path.join(result_path, 'reward_dists')
    os.makedirs(output_dir, exist_ok=True)

    values_list = list(instance_rewards.values())
    reward_types = values_list[0].keys()

    for reward_type in reward_types:
        plot_reward_distribution(
            instance_rewards,
            reward_type=f'{reward_type}',
            save_path=output_dir,
        )

    print(f'Done, reward dist file is saved in {output_dir}')


if __name__ == '__main__':

    file_path = None

    # read file
    with open(file_path) as f:
        lines = f.readlines()

    # update rewards
    updated_lines = []
    for line in lines:
        data = json.loads(line)
        rewards = calculate_rewards(
            data.get('instance_id'),
            data.get('gt_results'),
            data,
            methods=['file_reward', 'func_reward', 'line_reward', 'repair_reward'],
        )
        data['rewards'] = rewards
        sum_reward = sum(rewards.values())
        data['rewards']['sum_reward'] = sum_reward
        updated_lines.append(json.dumps(data))

    # mkdir
    os.makedirs(
        os.path.dirname(file_path.replace('yangzonghan-m2', 'wangshengjie-m2')),
        exist_ok=True,
    )
    with open(file_path.replace('yangzonghan-m2', 'wangshengjie-m2'), 'w') as f:
        for line in updated_lines:
            f.write(line + '\n')
