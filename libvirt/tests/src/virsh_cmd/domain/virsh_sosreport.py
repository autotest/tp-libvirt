import logging
import os
import re
import shutil
import subprocess
from pathlib import Path


def run(test, params, env):

    generate_sos_report = params.get("generate_sos_report", "no") == "yes"
    access_pool_stats = params.get("access_pool_stats", "no") == "yes"

    # Configuration
    LOG_PATTERNS = [
            r"nfsd.*NULL pointer dereference",
            r"kernel: BUG:.*nfsd",
            r"kernel: WARNING:.*nfsd",
            r"pool_stats.*crash",
            r"kernel: general protection fault.*nfsd"
            ]
    proc_file = "/proc/fs/nfsd/pool_stats"
    log_sources = ["/var/log/kern.log", "/var/log/syslog",
                   "journalctl -k --since='1 hour ago'"]

    def check_user_status():

        # check if user has permissions to run the command
        logging.debug("Check for the user privileges")
        if os.geteuid() != 0:
            test.fail("'sosreport' must be run with root privileges")

    def scan_logs():

        issues = []
        for source in log_sources:
            try:
                if source.startswith("journalctl"):
                    logs = subprocess.check_output(source.split(), universal_newlines=True)
                else:
                    with open(source, 'r') as f:
                        logs = f.read()

                for pattern in LOG_PATTERNS:
                    if re.search(pattern, logs, re.IGNORECASE):
                        issues.append(f"Found in {source}: {pattern}")

            except Exception as e:
                continue
        return issues

    if generate_sos_report:
        # check if the command available
        logging.debug("Check if 'sosreport' is installed")
        if not shutil.which('sosreport'):
            test.fail("sosreport command not found. \
                       Is the sos package installed?")

        check_user_status()

        report_path = None

        try:
            # Run command in non-interactive mode
            logging.debug("Starting sosreport collection...")
            result = subprocess.run(
                    ['sosreport', '--batch'],
                    capture_output=True,
                    universal_newlines=True, check=True)

            # Parse output for getting the report path
            for line in result.stdout.split('\n'):
                if 'tar.xz' in line:
                    report_path = line.split()[-1]
                    break

            logging.debug("* * * REPORT GENERATED * * *")

        except Exception as err:
            test.fail(err)

        # check for any trace messages after report generation
        dmesg = subprocess.run(['dmesg'],
                               capture_output=True,
                               universal_newlines=True, check=True)

        log_issues = scan_logs()
        if log_issues:
            logging.debug("Found potential crash indicators in logs")
            for issue in log_issues:
                logging.debug(" - %s" % issue)
            test.fail("potential crash indicators found in logs")
        else:
            logging.debug("Your sos report file has been generated"
                          "and saved in : %s" % report_path)

    if access_pool_stats:

        check_user_status()

        # Check for the file existence
        try:
            lsmod = subprocess.check_output(["lsmod"], universal_newlines=True)
            if "nfsd" not in lsmod:
                test.fail("nfsd module not loaded")

            proc_mount = Path(proc_file)
            if not proc_mount.exists():
                test.fail("%s file not present" % proc_file)

            logging.debug("NFSD appears healthy")

        except Exception as e:
            logging.debug(str(e))

        # check for crash indicators before accessing the file
        log_issues = scan_logs()
        if log_issues:
            logging.debug("Found potential crash indicators in logs")
            for issue in log_issues:
                logging.debug(" - %s" % issue)

        # Attempt to safely  read the target file
        try:
            with open(proc_file, 'r') as f:
                f.read(500)
            logging.debug("File read successfully")
        except Exception as e:
            test.fail("File access failed: %s" % str(e))

        # check for crash indicators after accessing the file
        log_issues = scan_logs()
        if log_issues:
            logging.debug("Found potential crash indicators in logs")
            for issue in log_issues:
                logging.debug(" - %s" % issue)

            test.fail("potential crash indicators found in logs")
