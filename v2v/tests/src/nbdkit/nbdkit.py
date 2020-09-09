import re
import os
import logging

from avocado.utils import process
from virttest import data_dir
from virttest.utils_v2v import multiple_versions_compare


def run(test, params, env):
    """
    Basic nbdkit tests
    """
    checkpoint = params.get('checkpoint')
    version_requried = params.get('version_requried')

    def test_filter_stats_fd_leak():
        """
        check if nbdkit-stats-filter leaks an fd
        """
        tmp_logfile = os.path.join(data_dir.get_tmp_dir(), "nbdkit-test.log")
        cmd = """
nbdkit -U - --filter=log --filter=stats sh - \
  logfile=/dev/null statsfile=/dev/null \
  --run 'qemu-io -r -f raw -c "r 0 1" $nbd' <<\EOF
  case $1 in
    get_size) echo 1m;;
    pread) ls -l /proc/$$/fd > %s
      dd iflag=count_bytes count=$3 if=/dev/zero || exit 1 ;;
    *) exit 2 ;;
  esac
EOF
""" % tmp_logfile

        process.run(cmd, shell=True)

        count = 0
        with open(tmp_logfile) as fd:
            ptn = r'(\d+)\s+->\s+\'(pipe|socket):'
            cont = fd.read()
            logging.debug('all fds:\n%s', cont)
            for i, _ in re.findall(ptn, cont):
                if int(i) > 2:
                    count += 1
        if count > 0:
            test.fail('nbdkit-stats-filter leaks %d fd' % count)

    if version_requried and not multiple_versions_compare(
            version_requried):
        test.cancel("Testing requries version: %s" % version_requried)
    if checkpoint == 'filter_stats_fd_leak':
        test_filter_stats_fd_leak()
    else:
        test.error('Not found testcase: %s' % checkpoint)
