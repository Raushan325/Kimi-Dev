__version__ = '2.1.7'

from kimidev.harness.constants import (
    KEY_INSTANCE_ID,
    KEY_MODEL,
    KEY_PREDICTION,
    MAP_REPO_VERSION_TO_SPECS,
)
from kimidev.harness.grading import (
    ResolvedStatus,
    TestStatus,
    compute_fail_to_pass,
    compute_pass_to_pass,
    get_eval_report,
    get_logs_eval,
    get_resolution_status,
)
from kimidev.harness.log_parsers import (
    MAP_REPO_TO_PARSER,
)
from kimidev.harness.utils import (
    get_environment_yml,
    get_requirements,
)
