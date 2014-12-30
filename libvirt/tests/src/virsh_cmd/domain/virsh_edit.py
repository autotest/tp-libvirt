import logging
from virttest import remote
from autotest.client.shared import error
from virttest import virsh
from virttest import aexpect
from virttest import utils_libvirtd
from virttest import utils_misc
from virttest.libvirt_xml import vm_xml


def run(test, params, env):
    """
    Test command: virsh edit.

    The command can edit XML configuration for a domain
    1.Prepare test environment,destroy or suspend a VM.
    2.When the libvirtd == "off", stop the libvirtd service.
    3.Perform virsh edit operation.
    4.Recover test environment.
    5.Confirm the test result.
    """
    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    domid = vm.get_id()
    domuuid = vm.get_uuid()

    libvirtd_stat = params.get("libvirtd", "on")
    vm_ref = params.get("edit_vm_ref")
    extra_option = params.get("edit_extra_param", "")
    status_error = params.get("status_error")
    edit_element = params.get("edit_element", "vcpu")

    vmxml = vm_xml.VMXML.new_from_inactive_dumpxml(vm_name)
    libvirtd = utils_libvirtd.Libvirtd()

    def exec_edit(source, edit_cmd):
        """
        Execute edit command.

        :param source : virsh edit's option.
        :param edit_cmd: Edit command list to execute.
        :return: True if edit successed, False if edit failed.
        """
        logging.info("Trying to edit xml with cmd %s", edit_cmd)
        session = aexpect.ShellSession("sudo -s")
        try:
            session.sendline("virsh -c %s edit %s" % (vm.connect_uri, source))
            for cmd in edit_cmd:
                session.sendline(cmd)
            session.send('\x1b')
            session.send('ZZ')
            remote.handle_prompts(session, None, None, r"[\#\$]\s*$", debug=True)
            session.close()
            return True
        except Exception, e:
            session.close()
            logging.error("Error occured: %s", e)
            return False

    def edit_vcpu(source):
        """
        Modify vm's cpu information by virsh edit command.

        :param source : virsh edit's option.
        :return: True if edit successed,False if edit failed.
        """
        vcpucount_result = virsh.vcpucount(vm_name,
                                           options="--config --maximum")
        if vcpucount_result.exit_status:
            # Fail back to libvirt_xml way to test vcpucount.
            original_vcpu = str(vmxml.vcpu)
        else:
            original_vcpu = vcpucount_result.stdout.strip()

        expected_vcpu = str(int(original_vcpu) + 1)
        dic_mode = {
            "edit": r":%s /[0-9]*<\/vcpu>/" + expected_vcpu + r"<\/vcpu>",
            "recover": r":%s /[0-9]*<\/vcpu>/" + original_vcpu + r"<\/vcpu>"}
        status = exec_edit(source, [dic_mode["edit"]])
        logging.info(status)
        if not status:
            return status
        if libvirtd_stat == "off":
            return False
        if params.get("paused_after_start_vm") == "yes":
            virsh.resume(vm_name, ignore_status=True)
            virsh.destroy(vm_name)
        elif params.get("start_vm") == "yes":
            virsh.destroy(vm_name)
        new_vcpus = str(vm_xml.VMXML.new_from_inactive_dumpxml(vm_name).vcpu)
        # Recover cpuinfo
        # Use name rather than source, since source could be domid
        status = exec_edit(vm_name, [dic_mode["recover"]])
        if status and new_vcpus != expected_vcpu:
            return False
        return status

    def edit_iface(vm_name):
        """
        Modify vm's interface information by virsh edit command.
        """
        iface_type = params.get("iface_type")
        iface_model = params.get("iface_model")
        edit_error = "yes" == params.get("edit_error", "no")
        if iface_type:
            edit_cmd = (r":%s /<interface type=.*>/<interface type='{0}'>/"
                        "".format(iface_type))
            status = exec_edit(vm_name, [edit_cmd])
        elif iface_model:
            edit_cmd = (r":/<interface/,/<\/interface>/s/<model type=.*\/>/"
                        "<model type='%s'\/>/" % iface_model)
            status = exec_edit(vm_name, [edit_cmd])

        if not status and not edit_error:
            logging.error("Expect success, but failure")
            return False
        if edit_error and status:
            logging.error("Expect error, but success")
            return False

        # Destroy domain and start it to check if vm can be started
        start_error = "yes" == params.get("start_error", "no")
        vm.destroy()
        ret = virsh.start(vm_name, ignore_status=True)
        if start_error and not ret.exit_status:
            logging.error("Vm started unexpectedly")
            return False
        if not start_error and ret.exit_status:
            logging.error("Vm failed to start")
            return False
        return True

    def edit_memory(source):
        """
        Modify vm's maximum and current memory(unit and value).

        :param source: virsh edit's option.
        :return: True if edit successed,False if edit failed.
        """
        mem_unit = params.get("mem_unit", "K")
        mem_value = params.get("mem_value", "1048576")
        mem_delta = params.get("mem_delta", 1000)
        edit_cmd = []
        del_cmd = r":g/currentMemory/d"
        edit_cmd.append(del_cmd)
        update_cmd = r":%s/<memory unit='KiB'>[0-9]*<\/memory>/<memory unit='"
        update_cmd += mem_unit + "'>" + mem_value + r"<\/memory>"
        edit_cmd.append(update_cmd)
        try:
            expected_mem = int(utils_misc.normalize_data_size(
                mem_value + mem_unit, 'K').split('.')[0])
        except ValueError:
            logging.error("Fail to translate %s to KiB", mem_value + mem_unit)
            return False
        logging.debug("Expected max memory is %s", expected_mem)
        status = exec_edit(source, edit_cmd)
        try:
            if status:
                # Restart vm to check memory value
                virsh.destroy(vm_name)
                virsh.start(vm_name)
                new_mem = vm.get_max_mem()
                if new_mem - expected_mem > int(mem_delta):
                    logging.error("New max memory %s is not excepted", new_mem)
                    return False
        except Exception, e:
            logging.error("Error occured when check domain memory: %s", e)
            return False
        return status

    # run test case
    if libvirtd_stat == "off":
        libvirtd.stop()

    if vm_ref == "id":
        vm_ref = domid
    elif vm_ref == "uuid":
        vm_ref = domuuid
    elif vm_ref == "name":
        vm_ref = vm_name
    if extra_option:
        vm_ref += extra_option

    try:
        if edit_element == "vcpu":
            status = edit_vcpu(vm_ref)
        elif edit_element == "memory":
            status = edit_memory(vm_ref)
        elif edit_element == "iface":
            status = edit_iface(vm_name)
        else:
            raise error.TestNAError("No edit method for %s" % edit_element)
        # check status_error
        if status_error == "yes":
            if status:
                raise error.TestFail("Run successfully with wrong command!")
        elif status_error == "no":
            if not status:
                raise error.TestFail("Run failed with right command")
    finally:
        # recover libvirtd service start
        if libvirtd_stat == "off":
            libvirtd.start()
        # Recover VM
        vm.destroy()
        vmxml.sync()
