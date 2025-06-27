import os
import time
from virttest.utils_test import libvirt
from virttest import utils_misc
from virttest import virsh
from virttest import data_dir


class VirshCmdCheck(object):
    """
    This class is an utility for testing ......

    :param vm: a libvirt_vm.VM class instance
    :param params: dict with the test parameters
    :param test: test object
    """
    def __init__(self, vm, vm_name, params, test, session=None):
        self.params = params
        self.test = test
        self.vm = vm
        self.vm_name = vm_name
        self.save_file = ""
        self.session = session

    def check_save_restore(self, save_file="", **virsh_dargs):
        """
        Test domain save and restore.
        """
        if save_file:
            self.save_file = save_file
        else:
            self.save_file = os.path.join(data_dir.get_data_dir(),
                                          "%s.save" % self.vm_name)
            self.test.log.debug(f"vm save_file name set to: {self.save_file}")

        # Save the domain.
        ret = virsh.save(self.vm_name, self.save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        self.test.log.debug(f"TEST_STEP: save of vm and command check passed")

        # Restore the domain.
        ret = virsh.restore(self.save_file, **virsh_dargs)
        libvirt.check_exit_status(ret)
        self.test.log.debug(f"TEST_STEP: restore of vm and command check passed")

    def check_disk_caches(self, session=None):
        """
        Test virsh command dommemstat related cases
        """

        if session:
            self.session = session
        # Get info from vm
        if not self.session:
            self.test.log.debug(f"Logging into {self.vm_name}")
            self.session = self.vm.wait_for_login()
            time.sleep(5)

        # Get info from virsh dommemstat command
        self.test.log.debug(f"TEST_STEP: getting dommemstat from {self.vm_name}")
        dommemstat_output = virsh.dommemstat(
                self.vm_name, debug=True).stdout_text.strip()
        dommemstat = {}
        for line in dommemstat_output.splitlines():
            k, v = line.strip().split(' ')
            dommemstat[k] = v

        meminfo_keys = ['Buffers', 'Cached', 'SwapCached']
        meminfo = {k: utils_misc.get_mem_info(self.session, k) for k in meminfo_keys}

        # from kernel commit: Buffers + Cached + SwapCached = disk_caches
        tmp_sum = meminfo['Buffers'] + meminfo['Cached'] + meminfo['SwapCached']
        self.test.log.info('Buffers %d + Cached %d + SwapCached %d = %d kb',
                           meminfo['Buffers'],
                           meminfo['Cached'],
                           meminfo['SwapCached'],
                           tmp_sum
                           )

        # Compare and make sure error is within allowable range
        self.test.log.info('disk_caches is %s', dommemstat['disk_caches'])
        allow_error = int(self.params.get('allow_error', 15))
        actual_error = (tmp_sum - int(dommemstat['disk_caches'])) / tmp_sum * 100
        self.test.log.debug('Actual error: %.2f%%', actual_error)
        if actual_error > allow_error:
            self.test.fail('Buffers + Cached + SwapCached (%d) '
                           'should be close to disk_caches (%s). '
                           'Allowable error: %.2f%%' % (tmp_sum, dommemstat['disk_caches'], allow_error)
                           )

        self.test.log.debug('Buffers + Cached + SwapCached (%d) '
                            'should be close to disk_caches (%s). '
                            'Allowable error: %.2f%%' % (tmp_sum, dommemstat['disk_caches'], allow_error)
                            )

    def teardown(self):
        if self.save_file and os.path.exists(self.save_file):
            os.remove(self.save_file)
