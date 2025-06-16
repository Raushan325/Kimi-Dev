from pathlib import Path
from typing import Any

from kimidev.harness.constants import (
    APPLY_PATCH_FAIL,
    APPLY_PATCH_PASS,
    FAIL_TO_FAIL,
    FAIL_TO_PASS,
    KEY_INSTANCE_ID,
    KEY_PREDICTION,
    PASS_TO_FAIL,
    PASS_TO_PASS,
    RESET_FAILED,
    TESTS_ERROR,
    TESTS_TIMEOUT,
    ResolvedStatus,
    TestStatus,
)
from kimidev.harness.test_spec import TestSpec
from kimidev.harness.log_parsers import MAP_REPO_TO_PARSER, parse_log_pytest_swesmith


# MARK: Utility functions
def test_passed(case: str, sm: dict[str, str]) -> bool:
    return case in sm and sm[case] in [TestStatus.PASSED.value, TestStatus.XFAIL.value]


def test_failed(case: str, sm: dict[str, str]) -> bool:
    return case not in sm or any(
        sm[case] == status for status in [TestStatus.FAILED.value, TestStatus.ERROR.value]
    )


# MARK: Evaluation report functions
# def get_logs_eval(log_fp: str) -> tuple[dict[str, str], bool]:
def get_logs_eval(log_raw_content: str, instance_id: str) -> tuple[dict[str, str], bool]:
    """
    Retrieve evaluation results for a task instance from its corresponding log file

    Args:
        # log_fp (str): path to log file
        log_raw_content (str): the content of the log file
    Returns:
        bool: whether the patch applied successfully
        dict: status map
    
    TODO(john-b-yang): Check this is working properly...
    """
    # Convert e.g. "logs/scikit-learn__scikit-learn-12421/test_output.txt" to "scikit-learn/scikit-learn"
    # sample_id = str(Path(log_fp).parent.stem)  # e.g. scikit-learn__scikit-learn-12421

    if "." in instance_id:
        now_prefix = instance_id.split(".")[0]
        repo = "-".join(now_prefix.replace("__", "/"))  # e.g. scikit-learn/scikit-learn
    else:    
        repo = "-".join(instance_id.replace("__", "/").split("-")[:-1])  # e.g. scikit-learn/scikit-learn
    log_parser = MAP_REPO_TO_PARSER.get(repo, parse_log_pytest_swesmith)

    # with open(log_fp) as f:
    # content = f.read()
    # TODO fix constant here

    applied_patch_bool = False
    if "." not in instance_id:
        applied_patch_bool = ("applied patch" not in log_raw_content.lower())

    if (
        any(
            [
                x in log_raw_content
                for x in [
                    APPLY_PATCH_FAIL,
                    RESET_FAILED,
                    TESTS_ERROR,
                    TESTS_TIMEOUT,
                    "Failed to reset task environment",
                ]
            ]
        )
        or (applied_patch_bool)
    ):
        # Eval patch was not applied successfully
        return {}, False, log_raw_content

    # Get status map of evaluation results
    if "." not in instance_id:
        log_raw_content = log_raw_content.split(f"{APPLY_PATCH_PASS} (pred)")[-1]

    return log_parser(log_raw_content), True, log_raw_content


def get_eval_tests_report(
    eval_sm: dict[str, str],
    gold_results: dict[str, str],
    calculate_to_fail: bool = False,
) -> dict[str, dict[str, list[str]]]:
    """
    Create a report based on failure/pass change from gold results to eval results.

    Args:
        eval_sm (dict): evaluation status map
        gold_results (dict): gold results
        calculate_to_fail (bool): whether to calculate metrics for "x to fail" tests
    Returns:
        report (dict): report of metrics

    Metric Definitions (Gold Result Pair + Eval Result):
    - Fail-Pass (F2P) + P: Success (Resolution)
    - Pass-Pass (P2P) + P: Success (Maintenance)
    - Fail-Pass (F2P) + F: Failure
    - Pass-Pass (P2P) + F: Failure

    Miscellaneous Definitions
    - Fail-Fail (F2F) + F: Failure Maintenance
    - Pass-Fail (P2F) + F: Not considered
    - Fail-Fail (F2F) + P: Success (Extra Credit)
    - Pass-Fail (P2F) + P: Not considered
    """
    # Calculate resolution metrics
    f2p_success = []
    f2p_failure = []
    for test_case in gold_results[FAIL_TO_PASS]:
        if test_passed(test_case, eval_sm):
            # Assume silent success for now (test case not in eval_sm)
            f2p_success.append(test_case)
        elif test_failed(test_case, eval_sm):
            f2p_failure.append(test_case)

    # Calculate maintenance metrics
    p2p_success = []
    p2p_failure = []
    for test_case in gold_results[PASS_TO_PASS]:
        if test_passed(test_case, eval_sm):
            p2p_success.append(test_case)
        elif test_failed(test_case, eval_sm):
            p2p_failure.append(test_case)

    results = {
        FAIL_TO_PASS: {
            "success": f2p_success,
            "failure": f2p_failure,
        },
        PASS_TO_PASS: {
            "success": p2p_success,
            "failure": p2p_failure,
        },
    }

    f2f_success = []
    f2f_failure = []
    p2f_success = []
    p2f_failure = []
    if calculate_to_fail:
        # Calculate "extra credit" metrics
        for test_case in gold_results[FAIL_TO_FAIL]:
            if test_passed(test_case, eval_sm):
                f2f_success.append(test_case)
            elif test_failed(test_case, eval_sm):
                f2f_failure.append(test_case)

        # Calculate not considered metrics
        for test_case in gold_results[PASS_TO_FAIL]:
            if test_passed(test_case, eval_sm):
                p2f_success.append(test_case)
            elif test_failed(test_case, eval_sm):
                p2f_failure.append(test_case)

    results.update(
        {
            FAIL_TO_FAIL: {
                "success": f2f_success,
                "failure": f2f_failure,
            },
            PASS_TO_FAIL: {
                "success": p2f_success,
                "failure": p2f_failure,
            },
        }
    )
    return results


def compute_fail_to_pass(report: dict[str, dict[str, Any]]) -> float:
    """
    Compute fail-to-pass metric. Accepts single report as argument.
    """
    total = len(report[FAIL_TO_PASS]["success"]) + len(report[FAIL_TO_PASS]["failure"])
    if total == 0:
        return 1
    return len(report[FAIL_TO_PASS]["success"]) / total


def compute_pass_to_pass(report: dict[str, dict[str, Any]]) -> float:
    """
    Compute pass-to-pass metric. Accepts single report as argument.
    """
    total = len(report[PASS_TO_PASS]["success"]) + len(report[PASS_TO_PASS]["failure"])
    if total == 0:
        # TODO: Don't factor in p2p metrics
        return 1
    return len(report[PASS_TO_PASS]["success"]) / total


def get_resolution_status(report: dict[str, dict[str, Any]], is_swesmith: bool = False) -> str:
    """
    Determine resolved status of an evaluation instance

    Criteria:
        - If fail-to-pass (Resolution) = 1 and pass-to-pass (Maintenance) = 1 -> FULL
        - If (fail-to-pass (Resolution) < 1 and > 0) and pass-to-pass (Maintenance) = 1 -> PARTIAL
        - Otherwise -> NO
    """
    f2p = compute_fail_to_pass(report)
    p2p = compute_pass_to_pass(report)

    if not is_swesmith:
        if f2p == 1 and p2p == 1:
            return ResolvedStatus.FULL.value
        elif f2p < 1 and f2p > 0 and p2p == 1:
            return ResolvedStatus.PARTIAL.value
        else:
            return ResolvedStatus.NO.value
    else:
        if f2p == 1:
            return ResolvedStatus.FULL.value
        elif f2p < 1 and f2p > 0:
            return ResolvedStatus.PARTIAL.value
        else:
            return ResolvedStatus.NO.value



def get_eval_report(
    test_spec: TestSpec,
    prediction: dict[str, str],
    log_raw_content: str,
    include_tests_status: bool,
) -> dict[str, Any]:
    """
    Generate a report of model evaluation results from a prediction, task instance,
    and evaluation log.

    Args:
        test_spec (dict): test spec containing keys "instance_id", "FAIL_TO_PASS", and "PASS_TO_PASS"
        prediction (dict): prediction containing keys "instance_id", "model_name_or_path", and "model_patch"
        log_path (str): path to evaluation log
        include_tests_status (bool): whether to include the status of each test in the returned report
    Returns:
        report (dict): report of metrics
    """
    report_map = {}

    instance_id = prediction[KEY_INSTANCE_ID]
    report_map[instance_id] = {
        "patch_is_None": False,
        "patch_exists": False,
        "patch_successfully_applied": False,
        "resolved": False,
    }

    # Check if the model patch exists
    if prediction[KEY_PREDICTION] is None:
        report_map[instance_id]["patch_is_None"] = True
        return report_map
    report_map[instance_id]["patch_exists"] = True

    # Get evaluation logs
    eval_sm, found, log_content = get_logs_eval(log_raw_content, instance_id)
    # eval_sm, found, log_content = get_logs_eval(log_path)

    if not found:
        return report_map
    
    report_map[instance_id]["raw_log_content"] = log_content
    report_map[instance_id]["patch_successfully_applied"] = True

    is_swesmith = False
    try:
        eval_ref = {
            KEY_INSTANCE_ID: test_spec.instance_id,
            FAIL_TO_PASS: test_spec.FAIL_TO_PASS,
            PASS_TO_PASS: test_spec.PASS_TO_PASS,
        }
    except:
        eval_ref = {
            KEY_INSTANCE_ID: test_spec["instance_id"],
            FAIL_TO_PASS: test_spec["FAIL_TO_PASS"],
            PASS_TO_PASS: test_spec["PASS_TO_PASS"],
        }
        is_swesmith = True

    report = get_eval_tests_report(eval_sm, eval_ref)
    if get_resolution_status(report, is_swesmith) == ResolvedStatus.FULL.value:
        report_map[instance_id]["resolved"] = True

    if include_tests_status:
        report_map[instance_id]["tests_status"] = report  # type: ignore
    
    return report_map

