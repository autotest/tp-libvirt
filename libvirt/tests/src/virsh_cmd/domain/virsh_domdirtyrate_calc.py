import time

from virttest import virsh
from virttest import libvirt_version
from virttest.utils_test import libvirt
from virttest.utils_stress import install_stressapptest


def run(test, params, env):
    """
    Test domdirtyrate-calc command, make sure function with it works well
    """

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
        session.close()

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
        dirty_rate = out_dict["dirtyrate.megabytes_per_second"]
        if abs(int(dirty_rate)/int(ram_size) - 1) > 0.75:
            test.fail("Dirty rate calculated %s has a big difference "
                      "with the ram size %s loaded in guest " % (dirty_rate, ram_size))

    vm_name = params.get("main_vm")
    status_error = "yes" == params.get("status_error", "no")
    option = params.get("option", " ")
    period = params.get("period", "1")
    ram_size = params.get("ram_size")
    num_of_sec = params.get("num_of_sec", 10000)
    calc_status = params.get("calc_status", "2")

    if not libvirt_version.is_libvirt_feature_supported(params):
        test.cancel("domdirtyrate-calc command is not supported "
                    "before version libvirt-7.3.0 ")

    try:
        vm = env.get_vm(vm_name)

        load_stress(vm)
        if "--seconds" in option:
            option = "--seconds %s" % period
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
