# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#   Copyright Red Hat
#
#   SPDX-License-Identifier: GPL-2.0
#
#   Author: smitterl@redhat.com
#
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
import platform


def patch_total_cpu_count_s390x(cpu_module):
    """
    On s390x, override the avocado utility functions because on an
    LPAR they would return the number of CPUs available on the CEC
    but not on the LPAR.

    :param cpu_module: the module after import in calling script
    """

    if platform.machine() == "s390x":
        online_before_test = cpu_module.online_count()

        def _online_before_test():
            return online_before_test
        cpu_module.total_cpus_count = _online_before_test
        cpu_module.total_count = _online_before_test
