import os

from virttest import data_dir
from virttest import libvirt_version
from virttest import virsh

from virttest.libvirt_xml import vm_xml

from provider.guest_os_booting import guest_os_booting_base

VIRSH_ARGS = {'ignore_status': False, 'debug': True}


def run(test, params, env):
    """
    Test'--reset-nvram' option can re-initialize NVRAM from its pristine
    template.
    """

    libvirt_version.is_libvirt_feature_supported(params)
    reset_action = params.get("reset_action")
    reset_func = eval("virsh.%s" % reset_action)

    vm_name = guest_os_booting_base.get_vm(params)
    vm = env.get_vm(vm_name)
    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm.name)
    bkxml = vmxml.copy()

    nvram_path = f"/var/lib/libvirt/qemu/nvram/{vm.name}_VARS.fd"
    save_path = os.path.join(data_dir.get_tmp_dir(), vm.name + '.save')

    try:
        pre_action = params.get('pre_action')
        if pre_action:
            test.log.info("TEST_STEP: %s the VM.", pre_action)
            pre_func = eval("virsh.%s" % pre_action)
            if pre_action == "save":
                pre_func_args = [vm.name, save_path]
            else:
                pre_func_args = [vm.name]
            pre_func(*pre_func_args)

        test.log.info("TEST_STEP: Erase nvram file contents.")
        open(nvram_path, 'w').close()
        st_size = os.stat(nvram_path).st_size
        if st_size != 0:
            test.fail("The size(%s) of the nvram file before resetting is "
                      "incorrect!" % st_size)

        test.log.info("TEST_STEP: Reset nvram file using %s function.",
                      reset_action)
        reset_opts = {"create": [vmxml.xml],
                      "restore": [save_path],
                      "start": [vm.name]}
        reset_func(*reset_opts.get(reset_action), options="--reset-nvram",
                   **VIRSH_ARGS)
        st_size = os.stat(nvram_path).st_size
        if st_size <= 0:
            test.fail("The size(%s) of the nvram file after resetting is "
                      "incorrect!" % st_size)

    finally:
        virsh.managedsave_remove(vm.name, debug=True)
        bkxml.sync()
