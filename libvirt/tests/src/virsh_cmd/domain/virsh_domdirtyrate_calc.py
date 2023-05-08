import time
import logging as log

from virttest import virsh
from virttest import libvirt_version
from virttest.libvirt_xml import vm_xml
from virttest.utils_test import libvirt
from virttest.utils_stress import install_stressapptest

logging = log.getLogger('avocado.' + __name__)


def run(test, params, env):
    """
    Test domdirtyrate-calc command, make sure function with it works well
    """

    def setup_dirty_ring():
        """
        Config the dirty ring in kvm features
        """
        if vm.is_alive:
            vm.destroy()
        dirty_ring_attr = {'kvm_dirty_ring_state': 'on', 'kvm_dirty_ring_size': dirty_ring_size}
        vm_xml.VMXML.set_vm_features(vm_name, **dirty_ring_attr)
        vm.start()
        vm.wait_for_login()

    def load_stress(vm):
        """
        Load stress in vm

        :param vm: the vm to be installed with stressapptest
        """
        install_stressapptest(vm)
        session = vm.wait_for_login()
        try:
            stress_cmd = "stressapptest -M %s -s %s > /dev/null &" % (ram_size, num_of_sec)
            session.cmd(stress_cmd)
        except Exception as e:
            test.fail("Loading stress failed: %s" % e)

    def check_dirty_rate(dirty_rate):
        """
        Check dirty rate value

        :param dirty_rate: the dirty rate value calculated by domdirtyrate_calc
        """
        if mode == "dirty-ring":
            tolerance = 1.5
        else:
            tolerance = 0.75

        if abs(int(dirty_rate)/int(ram_size) - 1) > tolerance:
            test.fail("Dirty rate calculated %s has a big difference "
                      "with the ram size %s loaded in guest "
                      % (dirty_rate, ram_size))

    def check_output():
        """
        Check the dirty rate reported by domstats
        """
        res = virsh.domstats(vm_name, "--dirtyrate", debug=True)
        out_list = res.stdout_text.strip().splitlines()[1:]
        out_dict = {}
        out_dict = dict(item.strip().split("=") for item in out_list)

        if out_dict["dirtyrate.calc_status"] != calc_status:
            test.fail("Calculating dirty rate should be completed "
                      "after %s seconds" % period)
        if out_dict["dirtyrate.calc_period"] != period:
            test.fail("Calculating period is not the same with "
                      "the setting period %s" % period)
        if mode and out_dict["dirtyrate.calc_mode"] != mode:
            test.fail("Calculating mode %s is not the same with the specified "
                      "mode %s" % (out_dict["dirtyrate.calc_mode"], mode))
        dirty_rate = out_dict["dirtyrate.megabytes_per_second"]
        if mode == "dirty-ring":
            for cpu_num in range(vm.get_cpu_count()):
                dirty_rate = out_dict["dirtyrate.vcpu.%s.megabytes_per_second" % cpu_num]
                check_dirty_rate(dirty_rate)
        else:
            check_dirty_rate(dirty_rate)

    vm_name = params.get("main_vm")
    status_error = "yes" == params.get("status_error", "no")
    option = params.get("option", " ")
    mode = params.get("mode")
    period = params.get("period", "1")
    ram_size = params.get("ram_size")
    num_of_sec = params.get("num_of_sec", 10000)
    calc_status = params.get("calc_status", "2")
    dirty_ring_size = params.get("dirty_ring_size")

    libvirt_version.is_libvirt_feature_supported(params)

    vm = env.get_vm(vm_name)
    if mode == 'dirty-ring':
        vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
        vmxml_backup = vmxml.copy()
        setup_dirty_ring()

    try:
        if "--seconds" in option:
            option = "--seconds %s" % period
        if mode:
            option += " --mode %s" % mode

        load_stress(vm)

        result = virsh.domdirtyrate_calc(vm_name, options=option, ignore_status=True, debug=True)

        time.sleep(int(period))
        libvirt.check_exit_status(result)

        if status_error:
            return
        else:
            check_output()

    finally:
        if vm.is_alive():
            vm.destroy()
        if mode == 'dirty-ring':
            vmxml_backup.sync()
