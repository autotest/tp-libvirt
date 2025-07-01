import logging
import re
from collections import defaultdict

from avocado.utils import process

LOG = logging.getLogger('avocado.test.' + __name__)


def summarize_ausearch_results(ausearch_start,
                               denial_types=["AVC", "USER_AVC", "SELINUX_ERR", "USER_SELINUX_ERR"],
                               log_ausearch_rows=True):
    """
    Summarizes SELinux AVC denials from ausearch output.

    This function extracts and summarize denials from the `ausearch` command
    based on user-specified denial types

    Parameters:
    - ausearch_start (str): The starting datetime for `ausearch` to filter logs.
    - denial_types (list, optional): A list of SELinux denial types to include in the search.
      Defaults to `["AVC", "USER_AVC", "SELINUX_ERR", "USER_SELINUX_ERR"]` if not provided.
    - log_ausearch_rows (bool): If True, logs the full ausearch output for debugging. Default is True.

    Returns:
    - str: A formatted summary of AVC denials in the format:
        "Total denials: <count>, Processes: <process_name> (<actions>); ..."
    Raises:
    - process.CmdError: If `ausearch` encounters an error other than "<no matches>".
    """

    # Construct the ausearch command with dynamic denial types
    ausearch_cmd = f"ausearch --input-logs {' '.join(f'-m {t}' for t in denial_types)} --start {ausearch_start}"
    avc_denied = process.run(ausearch_cmd, ignore_status=True, shell=True)

    if avc_denied.exit_status != 0 and avc_denied.stderr_text.strip() != "<no matches>":
        raise process.CmdError(ausearch_cmd, avc_denied.result)

    avc_stdout = avc_denied.stdout_text.strip()
    if not avc_stdout:
        return ""

    if log_ausearch_rows:
        LOG.debug(f"ausearch denied rows after save and restore:\n--------------------------\n{avc_stdout}\n")

    # Process returned stdout and generate summary
    avc_pattern = re.compile(r'avc:\s+denied\s+\{\s*([^}]+)\s*\}\s+for\s+pid=\d+\s+comm="?([^"\s]+)"?', re.IGNORECASE)

    # Dictionary to store process denial counts
    denial_summary = defaultdict(lambda: defaultdict(int))
    total_denials = 0

    # Read and process the log file
    for line in avc_stdout.splitlines():
        match = avc_pattern.search(line)
        if match:
            total_denials += 1
            action, avc_process = match.groups()
            denial_summary[avc_process][action.strip()] += 1

    summary = f"Total denials: {total_denials}, Processes: "

    for avc_process, actions in denial_summary.items():
        # potential output with denial counts:  summary += f" {avc_process}:" + ", ".join(f"{count} {action}" for action, count in denial_summary[avc_process].items())
        summary += f"{avc_process} (" + ", ".join(actions.keys()) + '); '

    return summary
